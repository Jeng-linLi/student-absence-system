# -*- coding: utf-8 -*-
"""
線上請假系統（原型）
學生提交 → 導師審批 → （必要時）院系審批 → 核准

本檔只放路由與業務流程；常數與純函式在 utils.py，SQLite 連線／查詢在 db.py。
"""
import os
import csv
import io
import hmac
import time
import secrets
import json
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, jsonify, send_from_directory, Response, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import smtplib
from email.message import EmailMessage

from db import (BASE_DIR, DB_PATH, UPLOAD_DIR, get_db, close_db,  # noqa: F401
                q, execute, init_db)
from utils import (FACULTY_THRESHOLD_DAYS, ALLOWED_EXT,
                   MAX_BACKDATE_DAYS, MAX_SINGLE_DAYS, MAX_ATTACHMENT_MB,
                   MAX_REASON_LEN, MAX_COMMENT_LEN, MAX_PHONE_LEN,
                   MIN_PASSWORD_LEN, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SEC,
                   ACADEMIC_YEAR_START_MONTH, SESSION_IDLE_MINUTES,
                   APP_VERSION, LEAVE_STATUSES,
                   parse_date, calc_days, required_level_for,
                   academic_year_start, allowed_file,
                   validate_password, temp_password, csv_safe,
                   valid_role, valid_username, valid_email, sniff_allowed_type)

app = Flask(__name__)
app.secret_key = os.environ.get('LEAVE_SECRET', 'dev-only-change-me-in-production')
app.config['MAX_CONTENT_LENGTH'] = MAX_ATTACHMENT_MB * 1024 * 1024
app.config['CSRF_ENABLED'] = True

# Session cookie 強化：HttpOnly 阻擋 JS 讀取、SameSite=Lax 阻擋跨站送出
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('LEAVE_COOKIE_SECURE', '0') == '1',
)

# 預設密鑰告警：生產環境務必以 LEAVE_SECRET 覆寫，否則 session 可被偽造
if os.environ.get('LEAVE_SECRET') is None:
    print('[安全警告] 使用預設 secret_key（LEAVE_SECRET 未設定）。'
          '僅限本地開發，切勿直接上線。')


@app.teardown_appcontext
def _close_db(e=None):
    close_db(e)


WEEKDAY_LABELS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']

# 示範課表（demo 學生共用）。正式環境應由 SIS / LMS 同步，而非硬編碼。
DEMO_TIMETABLE = [
    # weekday, 開始, 結束, 課程代碼, 類型, 教室, 備註
    (0, '10:30', '11:50', 'AIAA 2205-L01', 'Lecture', 'Rm 102, W4', ''),
    (0, '13:30', '14:50', 'UFUG 2104-L01', 'Lecture', 'Rm 222, W1', ''),
    (0, '16:30', '17:50', 'UCUG 1603-T01', 'Tutorial', 'Rm 103, E1', ''),
    (1, '09:00', '11:50', 'UCUG 1800-L03', 'Lecture', 'Rm 147, E1', ''),
    (1, '12:00', '12:50', 'UFUG 2104-T01', 'Tutorial', 'Rm 222, W1', ''),
    (1, '16:30', '17:50', 'UCUG 1603-L01', 'Lecture', 'Rm 222, W1', ''),
    (2, '10:30', '11:50', 'AIAA 2205-L01', 'Lecture', 'Rm 101, W1', ''),
    (2, '13:30', '14:50', 'UFUG 2104-L01', 'Lecture', 'Rm 222, W1', ''),
    (3, '12:30', '13:20', 'UFUG 1501-T01', 'Tutorial', 'Rm 233, W1', ''),
    (3, '15:00', '16:50', 'UFUG 1501-LA01', 'Lab',
     'Einstein Lab (E3-231B) / Newton Lab (E3-231C)', '以實際編班為準'),
    (4, '09:00', '11:50', 'UFUG 1501-L01', 'Lecture', 'Rm 233, W1', ''),
    (4, '12:00', '12:50', 'AIAA 1010-L01', 'Lecture', 'Lecture Hall B', ''),
]


def _day_halves(is_start, is_end, start_period, end_period):
    """回傳某一天受影響的上午 / 下午標記（依起訖時段推算）。"""
    am = pm = False
    if is_start and is_end:            # 單日假
        if start_period == 'AM':
            am = True
            pm = (end_period == 'PM')
        else:                          # 起於下午
            pm = True
            am = (end_period == 'AM')  # 下午→上午 視為涵蓋全天
    elif is_start:                     # 首日的剩餘時段
        if start_period == 'AM':
            am = pm = True
        else:
            pm = True
    elif is_end:                       # 末日的上午時段
        if end_period == 'PM':
            am = pm = True
        else:
            am = True
    else:                              # 中間日：全天
        am = pm = True
    return am, pm


def affected_courses_for(student_id, start_date, end_date,
                        start_period='AM', end_period='PM'):
    """依學生課表，算出請假區間內受影響的課程（按半天粒度）。"""
    try:
        s = date.fromisoformat(start_date)
        e = date.fromisoformat(end_date)
    except (ValueError, TypeError):
        return []
    if s > e:
        s, e = e, s
    rows = q('SELECT weekday, start_time, end_time, course_code, kind, room, note '
             'FROM timetable WHERE student_id=? ORDER BY weekday, start_time',
             (student_id,))
    by_wd = {}
    for r in rows:
        by_wd.setdefault(r['weekday'], []).append(dict(r))
    out = []
    d = s
    while d <= e:
        wd = d.weekday()
        am, pm = _day_halves(d == s, d == e, start_period, end_period)
        day_courses = []
        for c in by_wd.get(wd, []):
            is_am = c['start_time'] < '12:00'
            if (is_am and am) or ((not is_am) and pm):
                day_courses.append(c)
        if day_courses:
            period = 'AM' if (am and not pm) else ('PM' if (pm and not am) else 'FULL')
            out.append({'date': d.isoformat(), 'weekday': wd,
                        'weekday_label': WEEKDAY_LABELS[wd], 'period': period,
                        'courses': day_courses})
        d += timedelta(days=1)
    return out


def seed_timetable():
    """寫入示範課表（冪等：已存在則跳過）。正式環境請由 SIS / LMS 同步。"""
    if q('SELECT 1 FROM timetable LIMIT 1'):
        return
    for sid in q('SELECT id FROM users WHERE role=?', ('student',)):
        for wd, st, et, code, kind, room, note in DEMO_TIMETABLE:
            execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
                    'course_code, kind, room, note) VALUES (?,?,?,?,?,?,?,?)',
                    (sid['id'], wd, st, et, code, kind, room, note))


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _smtp_configured():
    """僅當三個必要變數都齊全才啟用 Email 通道。"""
    return bool(os.environ.get('LEAVE_SMTP_HOST') and os.environ.get('LEAVE_SMTP_USER')
                and os.environ.get('LEAVE_SMTP_FROM'))


def send_email(to_addr, subject, body):
    """失敗靜默：通知是「最好能有」，絕不能因郵件伺服器問題讓請假流程報錯。"""
    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = os.environ.get('LEAVE_SMTP_FROM', '')
        msg['To'] = to_addr
        msg.set_content(body)
        with smtplib.SMTP(os.environ['LEAVE_SMTP_HOST'],
                          int(os.environ.get('LEAVE_SMTP_PORT', '587'))) as s:
            if os.environ.get('LEAVE_SMTP_TLS', '1') == '1':
                s.starttls()
            if os.environ.get('LEAVE_SMTP_USER'):
                s.login(os.environ['LEAVE_SMTP_USER'], os.environ.get('LEAVE_SMTP_PASS', ''))
            s.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — 通知通道失敗不應影響主流程
        print(f'[通知] 郵件發送失敗（已忽略，不影響請假流程）：{exc}')


def notify(user_id, request_id, message):
    """站內通知永遠寫入；若已設定 SMTP，再額外寄一封 Email（失敗靜默）。"""
    if user_id:
        execute('INSERT INTO notifications (user_id, request_id, message) VALUES (?,?,?)',
                (user_id, request_id, message))
        if _smtp_configured():
            u = q('SELECT email FROM users WHERE id=?', (user_id,), one=True)
            if u and u['email']:
                send_email(u['email'], '線上請假系統通知', message)


def log_action(request_id, actor_id, action, detail=''):
    execute('INSERT INTO audit_logs (request_id, actor_id, action, detail) VALUES (?,?,?,?)',
            (request_id, actor_id, action, detail))


# --------------------------------------------------------------------------
# CSRF 防護
# --------------------------------------------------------------------------
def csrf_token():
    """取得（必要時產生）當前 session 的 CSRF token。"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


@app.before_request
def csrf_protect():
    """所有 POST 請求都必須帶上與 session 一致的 CSRF token。"""
    if request.method != 'POST':
        return
    if not app.config.get('CSRF_ENABLED', True):
        return
    expected = session.get('csrf_token')
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        abort(400, description='CSRF 驗證失敗，請重新載入頁面後再試。')


@app.errorhandler(400)
def bad_request(e):
    flash(getattr(e, 'description', '請求無效。'), 'error')
    return redirect(request.referrer or url_for('dashboard'))


@app.after_request
def security_headers(resp):
    """加上基本安全標頭；CSP 允許本專案所需的 inline style / script。"""
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'")
    # 動態頁面（含個人請假資料、附件）不進瀏覽器快取：
    # 登出後按「後退」不應復現資料，公共電腦也不留殘影。靜態資源除外。
    if request.endpoint and request.endpoint != 'static':
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


# --------------------------------------------------------------------------
# 登入限流（寫入資料庫：跨進程、重啟後仍有效）
# 舊版以進程內 dict 計數——重啟即清零，多 worker 部署時每個進程各記一份，
# 限流形同虛設；改以 SQLite 持久化（正式環境換 Redis 時沿用同一組介面即可）。
# --------------------------------------------------------------------------
def login_throttled(key):
    cutoff = time.time() - LOGIN_WINDOW_SEC
    execute('DELETE FROM login_attempts WHERE ts < ?', (cutoff,))
    row = q('SELECT COUNT(*) c FROM login_attempts WHERE key=?', (key,), one=True)
    return row['c'] >= LOGIN_MAX_ATTEMPTS


def login_failed(key):
    execute('INSERT INTO login_attempts (key, ts) VALUES (?,?)', (key, time.time()))


def login_succeeded(key):
    execute('DELETE FROM login_attempts WHERE key=?', (key,))


# --------------------------------------------------------------------------
# 業務校驗
# --------------------------------------------------------------------------
def overlapping_requests(student_id, start_date, end_date, exclude_id=None):
    """找出與指定區間重疊、且仍有效的假單（已駁回/已撤回不算）。"""
    sql = ("SELECT id, start_date, end_date FROM leave_requests "
           "WHERE student_id=? AND status IN ('pending','approved') "
           "AND date(start_date) <= date(?) AND date(end_date) >= date(?)")
    args = [student_id, end_date.isoformat(), start_date.isoformat()]
    if exclude_id:
        sql += ' AND id<>?'
        args.append(exclude_id)
    return q(sql, args)


def role_scope(u):
    """統計／匯出的資料範圍：導師只看自己的學生、院系只看本院系、管理員看全部。

    回傳 (條件列表, args)，可直接組成 WHERE 子句。
    """
    if u['role'] == 'advisor':
        return ['s.advisor_id=?'], [u['id']]
    if u['role'] == 'faculty':
        return ['s.department=?'], [u['department']]
    return [], []


def _now_str():
    """統一時間字串格式（含微秒：auth_at 與 pwd_changed_at 只差不到一秒時
    秒級格式會判為相等，導致「重設密碼後舊 session 仍有效」的競態）。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')


def _parse_ts(s):
    """容忍有無微秒兩種格式。"""
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f')
    except ValueError:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')


def current_user():
    """回傳目前登入者；session 閒置逾時或登入後密碼被改過則一律視為未登入。"""
    if 'uid' not in session:
        return None
    u = q('SELECT * FROM users WHERE id=? AND active=1', (session['uid'],), one=True)
    if not u:
        return None
    now = datetime.now()
    # 閒置逾時：session 是簽名 cookie，服務端無法主動逐出，
    # 只能每次請求刷新／檢驗 last_seen 時間戳記。
    last_seen = session.get('last_seen')
    if last_seen:
        try:
            idle = (now - _parse_ts(last_seen)).total_seconds()
        except ValueError:
            idle = 0
        if idle > SESSION_IDLE_MINUTES * 60:
            session.clear()
            return None
    # 本次登入後密碼被改過（管理員重設、或於別處改密碼）→ 舊 session 即時作廢
    if u['pwd_changed_at'] and session.get('auth_at', '') < u['pwd_changed_at']:
        session.clear()
        return None
    session['last_seen'] = now.strftime('%Y-%m-%d %H:%M:%S.%f')
    return u


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for('login'))
        return f(*a, **kw)
    return wrapper


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for('login'))
            if u['role'] not in roles:
                flash('你沒有權限訪問該頁面。', 'error')
                return redirect(url_for('dashboard'))
            return f(*a, **kw)
        return wrapper
    return deco


@app.context_processor
def inject_globals():
    u = current_user()
    unread = 0
    if u:
        row = q('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0',
                (u['id'],), one=True)
        unread = row['c']
    return dict(current_user=u, unread_count=unread, app_version=APP_VERSION,
                now=datetime.now(), FACULTY_THRESHOLD_DAYS=FACULTY_THRESHOLD_DAYS,
                min_date=(date.today() - timedelta(days=MAX_BACKDATE_DAYS)).isoformat(),
                max_date=(date.today() + timedelta(days=365)).isoformat(),
                MAX_SINGLE_DAYS=MAX_SINGLE_DAYS, MAX_BACKDATE_DAYS=MAX_BACKDATE_DAYS,
                MAX_REASON_LEN=MAX_REASON_LEN, MAX_COMMENT_LEN=MAX_COMMENT_LEN,
                MAX_PHONE_LEN=MAX_PHONE_LEN)


# --------------------------------------------------------------------------
# 狀態標籤
# --------------------------------------------------------------------------
STATUS_LABEL = {
    'pending': '審批中', 'approved': '已核准',
    'rejected': '已駁回', 'withdrawn': '已撤回',
}
STATUS_TONE = {
    'pending': 'amber', 'approved': 'green',
    'rejected': 'red', 'withdrawn': 'grey',
}


def status_view(req):
    """產生人間可讀的狀態（含目前審到哪一級）。"""
    if req['status'] == 'pending':
        return f"待{'院系' if req['current_level'] == 2 else '導師'}審批"
    return STATUS_LABEL.get(req['status'], req['status'])


app.jinja_env.globals.update(status_view=status_view,
                             STATUS_TONE=STATUS_TONE,
                             STATUS_LABEL=STATUS_LABEL,
                             csrf_token=csrf_token)


# --------------------------------------------------------------------------
# 登入 / 登出
# --------------------------------------------------------------------------
# 時序均化：帳號不存在時也執行一次雜湊比對，避免以回應時間差枚舉有效帳號
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(16))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        uname = username.lower()
        # 兩把鎖：
        #  ① IP + 帳號——阻斷單一來源對單一帳號的連續衝撞（v0.8.0 原有）；
        #  ② 僅帳號——攻擊者換 IP／漫遊 Proxy 時，同一帳號的失敗仍累計。
        # 只靠 ① 的話，分散式密碼填充可以每個 IP 試 7 次繞過。
        key = f"{request.remote_addr}:{uname}"
        user_key = f"user:{uname}"
        if login_throttled(key) or login_throttled(user_key):
            flash(f'登入失敗次數過多，請 {LOGIN_WINDOW_SEC // 60} 分鐘後再試。', 'error')
            return render_template('login.html'), 429
        user = q('SELECT * FROM users WHERE username=? AND active=1', (username,), one=True)
        if user and check_password_hash(user['password_hash'], password):
            login_succeeded(key)
            login_succeeded(user_key)
            session.clear()                       # 防 session fixation
            session['uid'] = user['id']
            session['csrf_token'] = secrets.token_hex(32)
            session['auth_at'] = _now_str()
            session['last_seen'] = session['auth_at']
            return redirect(url_for('dashboard'))
        if not user:
            # 帳號不存在也跑一次雜湊比對，拉平與「帳號存在但密碼錯誤」的時間差
            check_password_hash(_DUMMY_PASSWORD_HASH, password)
        login_failed(key)
        login_failed(user_key)
        flash('帳號或密碼錯誤。', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/password', methods=['GET', 'POST'])
@login_required
def change_password():
    """所有角色都共用初始密碼 Demo@2026，必須能自行修改。

    注意：舊版示範密碼 Pass@123 已列入弱密碼擋鎖（v0.9.0），
    新舊帳號都不允許再把密碼改回它。
    """
    u = current_user()
    if request.method == 'POST':
        old = request.form.get('old_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        err = validate_password(new, u['username'])
        if err:
            # 與其他錯誤路徑一致：flash 後直接重渲染表單（不中斷使用者操作）
            flash(err, 'error')
        elif not check_password_hash(u['password_hash'], old):
            flash('目前密碼不正確。', 'error')
        elif new == old:
            flash('新密碼不可與目前密碼相同。', 'error')
        elif new != confirm:
            flash('兩次輸入的新密碼不一致。', 'error')
        else:
            execute('UPDATE users SET password_hash=?, pwd_changed_at=? WHERE id=?',
                    (generate_password_hash(new), _now_str(), u['id']))
            log_action(None, u['id'], 'change_password', '使用者自行修改密碼')
            flash('密碼已更新，請用新密碼重新登入。', 'success')
            return redirect(url_for('logout'))
    return render_template('change_password.html')


def count_todo(u):
    """待當前用戶審批的假單數量。"""
    if u['role'] == 'advisor':
        return q("SELECT COUNT(*) c FROM leave_requests r JOIN users s ON s.id=r.student_id "
                 "WHERE r.status='pending' AND r.current_level=1 AND s.advisor_id=?",
                 (u['id'],), one=True)['c']
    if u['role'] == 'faculty':
        return q("SELECT COUNT(*) c FROM leave_requests r JOIN users s ON s.id=r.student_id "
                 "WHERE r.status='pending' AND r.current_level=2 AND s.department=?",
                 (u['department'],), one=True)['c']
    return q("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'", one=True)['c']


# --------------------------------------------------------------------------
# 儀表板
# --------------------------------------------------------------------------
@app.route('/')
@login_required
def dashboard():
    u = current_user()
    ctx = {'pending_mine': 0, 'todo': 0, 'recent': [], 'stats': {},
           'my_leave_days': 0, 'year_leave_days': 0, 'year_start': ''}

    if u['role'] == 'student':
        ctx['pending_mine'] = q('SELECT COUNT(*) c FROM leave_requests WHERE student_id=? '
                                "AND status='pending'", (u['id'],), one=True)['c']
        ctx['my_leave_days'] = q("SELECT COALESCE(SUM(days),0) s FROM leave_requests "
                                 "WHERE student_id=? AND status='approved'", (u['id'],), one=True)['s']
        ctx['year_leave_days'] = q("SELECT COALESCE(SUM(days),0) s FROM leave_requests "
                                   "WHERE student_id=? AND status='approved' "
                                   "AND date(start_date) >= date(?)",
                                   (u['id'], academic_year_start().isoformat()), one=True)['s']
        ctx['year_start'] = academic_year_start().isoformat()
        ctx['recent'] = q('SELECT r.*, t.name type_name FROM leave_requests r '
                          'JOIN leave_types t ON t.id=r.leave_type_id '
                          'WHERE r.student_id=? ORDER BY r.created_at DESC LIMIT 5', (u['id'],))
    else:
        ctx['todo'] = count_todo(u)
        ctx['recent'] = todo_requests(u, limit=5)
        # 全系統統計只給審批者／管理員看；學生沒有必要知道別人的請假彙總
        ctx['stats'] = {
            'total': q('SELECT COUNT(*) c FROM leave_requests', one=True)['c'],
            'pending': q("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'", one=True)['c'],
            'approved': q("SELECT COUNT(*) c FROM leave_requests WHERE status='approved'", one=True)['c'],
            'rejected': q("SELECT COUNT(*) c FROM leave_requests WHERE status='rejected'", one=True)['c'],
        }
    return render_template('dashboard.html', **ctx)


def todo_requests(u, limit=None):
    """待當前用戶審批的假單。"""
    if u['role'] == 'advisor':
        sql = ('SELECT r.*, t.name type_name, s.name student_name, s.class_name '
               'FROM leave_requests r '
               'JOIN leave_types t ON t.id=r.leave_type_id '
               'JOIN users s ON s.id=r.student_id '
               "WHERE r.status='pending' AND r.current_level=1 AND s.advisor_id=? "
               'ORDER BY r.created_at ASC')
        args = (u['id'],)
    elif u['role'] == 'faculty':
        sql = ('SELECT r.*, t.name type_name, s.name student_name, s.class_name '
               'FROM leave_requests r '
               'JOIN leave_types t ON t.id=r.leave_type_id '
               'JOIN users s ON s.id=r.student_id '
               "WHERE r.status='pending' AND r.current_level=2 AND s.department=? "
               'ORDER BY r.created_at ASC')
        args = (u['department'],)
    else:
        sql = ('SELECT r.*, t.name type_name, s.name student_name, s.class_name '
               'FROM leave_requests r '
               'JOIN leave_types t ON t.id=r.leave_type_id '
               'JOIN users s ON s.id=r.student_id '
               "WHERE r.status='pending' ORDER BY r.created_at ASC")
        args = ()
    if limit:
        sql += f' LIMIT {int(limit)}'
    return q(sql, args)


# --------------------------------------------------------------------------
# 我的假單
# --------------------------------------------------------------------------
@app.route('/my')
@login_required
def my_requests():
    u = current_user()
    status = request.args.get('status', '')
    if status not in LEAVE_STATUSES:      # 白名單外的一律視為不篩選
        status = ''
    page = request.args.get('page', 1, type=int)
    per_page = 20

    where = 'WHERE r.student_id=?'
    args = [u['id']]
    if status:
        where += ' AND r.status=?'
        args.append(status)

    total = q('SELECT COUNT(*) c FROM leave_requests r ' + where, args, one=True)['c']
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)

    rows = q('SELECT r.*, t.name type_name FROM leave_requests r '
             'JOIN leave_types t ON t.id=r.leave_type_id '
             f'{where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?',
             args + [per_page, (page - 1) * per_page])
    return render_template('my_requests.html', requests=rows, filter_status=status,
                           page=page, total_pages=total_pages, total=total)


# --------------------------------------------------------------------------
# 新建假單
# --------------------------------------------------------------------------
@app.route('/new', methods=['GET', 'POST'])
@login_required
def new_request():
    u = current_user()
    if u['role'] != 'student':
        flash('只有學生可以提交假單。', 'error')
        return redirect(url_for('dashboard'))
    types = q('SELECT * FROM leave_types WHERE active=1 ORDER BY id')

    if request.method == 'POST':
        try:
            type_id = int(request.form['leave_type_id'])
            start_date = parse_date(request.form['start_date'])
            end_date = parse_date(request.form['end_date'])
            sp = request.form.get('start_period', 'AM')
            ep = request.form.get('end_period', 'PM')
        except (KeyError, ValueError):
            flash('表單填寫有誤，請檢查日期。', 'error')
            return render_template('new_request.html', types=types, form=request.form)

        reason = request.form.get('reason', '').strip()
        phone = request.form.get('contact_phone', '').strip()
        ltype = q('SELECT * FROM leave_types WHERE id=?', (type_id,), one=True)
        days = calc_days(start_date, end_date, sp, ep)

        today = date.today()
        err = None
        if not ltype:
            err = '請選擇有效的假別。'
        elif end_date < start_date:
            err = '結束日期不能早於開始日期。'
        elif days <= 0:
            err = '請假天數計算異常，請檢查日期與上午/下午設定。'
        elif days > MAX_SINGLE_DAYS:
            err = f'單張假單不得超過 {MAX_SINGLE_DAYS} 天，請確認日期是否填錯。'
        elif (today - end_date).days > MAX_BACKDATE_DAYS:
            err = f'不可補請超過 {MAX_BACKDATE_DAYS} 天前的假單。'
        elif not reason:
            err = '請填寫請假事由。'
        elif len(reason) > MAX_REASON_LEN:
            err = f'請假事由過長（上限 {MAX_REASON_LEN} 字）。'
        elif len(phone) > MAX_PHONE_LEN:
            err = f'聯絡電話過長（上限 {MAX_PHONE_LEN} 字）。'
        else:
            # 其餘校驗都通過才查資料庫（整個流程只查一次重疊）
            clashes = overlapping_requests(u['id'], start_date, end_date)
            if clashes:
                err = (f'你在此區間已有假單 #{clashes[0]["id"]}'
                       f'（{clashes[0]["start_date"]} ~ {clashes[0]["end_date"]}），'
                       '請先撤回或修改日期。')
        if err:
            flash(err, 'error')
            return render_template('new_request.html', types=types, form=request.form)

        attachment = ''
        file = request.files.get('attachment')
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('證明文件格式不支援（僅 PDF/圖片/Word）。', 'error')
                return render_template('new_request.html', types=types, form=request.form)
            # 副檔名說謊防護：改檔名很容易，改檔案頭很難——依魔術位元組驗證真實格式
            head = file.stream.read(8)
            file.stream.seek(0)
            if not sniff_allowed_type(head):
                flash('證明文件內容與宣稱的格式不符（僅接受真正的 PDF／圖片／Word 文件）。',
                      'error')
                return render_template('new_request.html', types=types, form=request.form)
            # 檔名加隨機字串，避免附件 URL 被猜測枚舉
            fn = secure_filename(
                f"{u['username']}_{datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(6)}_{file.filename}")
            file.save(os.path.join(UPLOAD_DIR, fn))
            attachment = fn
        if ltype['requires_attachment'] and not attachment:
            flash(f"「{ltype['name']}」必須上傳證明文件。", 'error')
            return render_template('new_request.html', types=types, form=request.form)

        level = required_level_for(days, ltype)
        affected = affected_courses_for(u['id'], start_date.isoformat(),
                                        end_date.isoformat(), sp, ep)
        affected_json = json.dumps(affected, ensure_ascii=False)
        rid = execute(
            'INSERT INTO leave_requests (student_id, leave_type_id, start_date, end_date, '
            'start_period, end_period, days, reason, attachment, contact_phone, '
            'affected_courses, status, current_level, required_level) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (u['id'], type_id, start_date.isoformat(), end_date.isoformat(), sp, ep,
             days, reason, attachment, phone, affected_json, 'pending', 1, level))
        execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 1, 'pending'))
        if level == 2:
            execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 2, 'pending'))

        log_action(rid, u['id'], 'submit', f"提交{ltype['name']} {days}天，審批層級{level}")
        if u['advisor_id']:
            notify(u['advisor_id'], rid, f"{u['name']} 提交了 {days} 天{ltype['name']}，待你審批。")
        flash('假單已提交，等待導師審批。', 'success')
        return redirect(url_for('detail', rid=rid))

    return render_template('new_request.html', types=types, form={})


@app.route('/api/affected_courses')
@login_required
def api_affected_courses():
    u = current_user()
    if u['role'] != 'student':
        return jsonify([])
    sd = request.args.get('start_date', '')
    ed = request.args.get('end_date', '')
    sp = request.args.get('start_period', 'AM')
    ep = request.args.get('end_period', 'PM')
    data = affected_courses_for(u['id'], sd, ed, sp, ep)
    return jsonify(data)


# --------------------------------------------------------------------------
# 假單詳情
# --------------------------------------------------------------------------
@app.route('/request/<int:rid>')
@login_required
def detail(rid):
    u = current_user()
    req = q('SELECT r.*, t.name type_name, t.requires_attachment, '
            's.name student_name, s.username student_no, s.class_name, s.department, '
            'a.name advisor_name '
            'FROM leave_requests r '
            'JOIN leave_types t ON t.id=r.leave_type_id '
            'JOIN users s ON s.id=r.student_id '
            'LEFT JOIN users a ON a.id=s.advisor_id '
            'WHERE r.id=?', (rid,), one=True)
    if not req:
        flash('找不到該假單。', 'error')
        return redirect(url_for('dashboard'))

    if u['role'] == 'student' and req['student_id'] != u['id']:
        flash('你沒有權限查看他人的假單。', 'error')
        return redirect(url_for('dashboard'))

    approvals = q('SELECT ap.*, u.name approver_name, u.role approver_role '
                  'FROM approvals ap LEFT JOIN users u ON u.id=ap.approver_id '
                  'WHERE ap.request_id=? ORDER BY ap.level', (rid,))
    logs = q('SELECT l.*, u.name actor_name FROM audit_logs l '
             'LEFT JOIN users u ON u.id=l.actor_id WHERE l.request_id=? '
             'ORDER BY l.created_at DESC, l.id DESC', (rid,))

    can_approve = False
    if req['status'] == 'pending':
        if u['role'] == 'advisor' and req['current_level'] == 1:
            stu = q('SELECT advisor_id FROM users WHERE id=?', (req['student_id'],), one=True)
            can_approve = bool(stu and stu['advisor_id'] == u['id'])
        elif u['role'] == 'faculty' and req['current_level'] == 2:
            can_approve = (req['department'] == u['department'])
        elif u['role'] == 'admin':
            can_approve = True

    affected = []
    try:
        affected = json.loads(req['affected_courses'] or '[]')
    except (ValueError, TypeError):
        affected = []
    return render_template('request_detail.html', req=req, approvals=approvals,
                           logs=logs, can_approve=can_approve, affected=affected)


# --------------------------------------------------------------------------
# 審批動作
# --------------------------------------------------------------------------
@app.route('/request/<int:rid>/decide', methods=['POST'])
@login_required
def decide(rid):
    u = current_user()
    req = q('SELECT r.*, t.name type_name, s.name student_name, s.department, s.advisor_id '
            'FROM leave_requests r JOIN leave_types t ON t.id=r.leave_type_id '
            'JOIN users s ON s.id=r.student_id WHERE r.id=?', (rid,), one=True)
    if not req or req['status'] != 'pending':
        flash('該假單已完成審批，無法再操作。', 'error')
        return redirect(url_for('detail', rid=rid))

    level = req['current_level']
    # 權限校驗
    if u['role'] == 'advisor':
        if level != 1 or req['advisor_id'] != u['id']:
            flash('你沒有權限審批這一單。', 'error')
            return redirect(url_for('dashboard'))
    elif u['role'] == 'faculty':
        if level != 2 or req['department'] != u['department']:
            flash('你沒有權限審批這一單。', 'error')
            return redirect(url_for('dashboard'))
    elif u['role'] != 'admin':
        flash('你沒有審批權限。', 'error')
        return redirect(url_for('dashboard'))

    action = request.form.get('action')
    comment = request.form.get('comment', '').strip()
    if action not in ('approve', 'reject'):
        flash('無效的操作。', 'error')
        return redirect(url_for('detail', rid=rid))
    if len(comment) > MAX_COMMENT_LEN:
        flash(f'審批意見過長（上限 {MAX_COMMENT_LEN} 字）。', 'error')
        return redirect(url_for('detail', rid=rid))
    if action == 'reject' and not comment:
        flash('駁回必須填寫理由。', 'error')
        return redirect(url_for('detail', rid=rid))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute('UPDATE approvals SET action=?, approver_id=?, comment=?, acted_at=? '
            'WHERE request_id=? AND level=?', (action, u['id'], comment, now, rid, level))

    if action == 'reject':
        execute("UPDATE leave_requests SET status='rejected', updated_at=? WHERE id=?", (now, rid))
        log_action(rid, u['id'], 'reject', comment)
        notify(req['student_id'], rid,
               f"你的{req['type_name']}假單被{'導師' if level == 1 else '院系'}駁回：{comment}")
        flash('已駁回該假單。', 'success')
        return redirect(url_for('approvals'))

    # 核准
    if level < req['required_level']:
        execute('UPDATE leave_requests SET current_level=?, updated_at=? WHERE id=?',
                (level + 1, now, rid))
        log_action(rid, u['id'], 'approve', f'第{level}級核准，轉院系複核')
        for f in q("SELECT id FROM users WHERE role='faculty' AND active=1 AND department=?",
                   (req['department'],)):
            notify(f['id'], rid, f"{req['student_name']} 的 {req['days']} 天{req['type_name']}待院系複核。")
        flash('已核准，已轉交院系複核。', 'success')
    else:
        execute("UPDATE leave_requests SET status='approved', updated_at=? WHERE id=?", (now, rid))
        log_action(rid, u['id'], 'approve', f'第{level}級核准，假單生效')
        notify(req['student_id'], rid, f"你的{req['type_name']}假單已核准（{req['days']} 天）。")
        flash('已核准該假單。', 'success')
    return redirect(url_for('approvals'))


@app.route('/request/<int:rid>/withdraw', methods=['POST'])
@login_required
def withdraw(rid):
    u = current_user()
    req = q('SELECT * FROM leave_requests WHERE id=?', (rid,), one=True)
    if not req or req['student_id'] != u['id']:
        flash('只能撤回自己的假單。', 'error')
        return redirect(url_for('dashboard'))
    if req['status'] != 'pending':
        flash('該假單已結案，無法撤回。', 'error')
        return redirect(url_for('detail', rid=rid))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute("UPDATE leave_requests SET status='withdrawn', updated_at=? WHERE id=?", (now, rid))
    log_action(rid, u['id'], 'withdraw', '學生撤回')
    flash('假單已撤回。', 'success')
    return redirect(url_for('my_requests'))


# --------------------------------------------------------------------------
# 待我審批 / 全部假單
# --------------------------------------------------------------------------
@app.route('/approvals')
@login_required
def approvals():
    u = current_user()
    scope = request.args.get('scope', 'todo')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total_pages = 1

    if scope == 'all':
        total = q('SELECT COUNT(*) c FROM leave_requests', one=True)['c']
        total_pages = max((total + per_page - 1) // per_page, 1)
        page = min(max(page, 1), total_pages)
        rows = q('SELECT r.*, t.name type_name, s.name student_name, s.class_name, s.department '
                 'FROM leave_requests r '
                 'JOIN leave_types t ON t.id=r.leave_type_id '
                 'JOIN users s ON s.id=r.student_id '
                 'ORDER BY r.created_at DESC LIMIT ? OFFSET ?',
                 (per_page, (page - 1) * per_page))
    else:
        rows = todo_requests(u)
        page = 1
    return render_template('approvals.html', requests=rows, scope=scope,
                           page=page, total_pages=total_pages)


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
@app.route('/notifications')
@login_required
def notifications():
    u = current_user()
    cur_filter = request.args.get('filter', 'all')
    if cur_filter not in ('all', 'unread'):
        cur_filter = 'all'
    page = request.args.get('page', 1, type=int)
    per_page = 20
    where = 'WHERE n.user_id=?'
    args = [u['id']]
    if cur_filter == 'unread':
        where += ' AND n.is_read=0'
    total = q(f'SELECT COUNT(*) c FROM notifications n {where}', args, one=True)['c']
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)
    rows = q('SELECT n.*, r.start_date FROM notifications n '
             'LEFT JOIN leave_requests r ON r.id=n.request_id '
             f'{where} ORDER BY n.created_at DESC, n.id DESC LIMIT ? OFFSET ?',
             tuple(args) + (per_page, (page - 1) * per_page))
    return render_template('notifications.html', items=rows, page=page,
                           total_pages=total_pages, total=total,
                           cur_filter=cur_filter)


@app.route('/notifications/read', methods=['POST'])
@login_required
def notifications_read_all():
    """「全部標為已讀」：CSRF 已由 before_request 統一防護。"""
    u = current_user()
    execute('UPDATE notifications SET is_read=1 WHERE user_id=? AND is_read=0', (u['id'],))
    flash('已將全部通知標為已讀。', 'success')
    return redirect(url_for('notifications'))


# --------------------------------------------------------------------------
# 學生請假統計（導師/院系/管理員）
# --------------------------------------------------------------------------
@app.route('/stats')
@login_required
@role_required('advisor', 'faculty', 'admin')
def stats():
    u = current_user()
    conds, args = role_scope(u)
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    rows = q(f'SELECT s.id, s.name, s.username, s.class_name, s.department, '
             f'COUNT(r.id) total_cnt, '
             f"COALESCE(SUM(CASE WHEN r.status='approved' THEN r.days ELSE 0 END),0) approved_days, "
             f"COALESCE(SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END),0) pending_cnt, "
             f"COALESCE(SUM(CASE WHEN r.status='rejected' THEN 1 ELSE 0 END),0) rejected_cnt "
             f'FROM users s LEFT JOIN leave_requests r ON r.student_id=s.id '
             f"{where} GROUP BY s.id HAVING total_cnt>0 ORDER BY approved_days DESC", args)

    # 假別統計同樣受範圍限制（導師只看自己的學生、院系只看本院系）
    type_conds, type_args = role_scope(u)
    type_conds.append("r.status='approved'")
    by_type = q("SELECT t.name, COUNT(r.id) cnt, COALESCE(SUM(r.days),0) days "
                'FROM leave_requests r JOIN leave_types t ON t.id=r.leave_type_id '
                'JOIN users s ON s.id=r.student_id '
                f"WHERE {' AND '.join(type_conds)} GROUP BY t.id ORDER BY days DESC",
                type_args)
    return render_template('stats.html', rows=rows, by_type=by_type)


@app.route('/export')
@login_required
@role_required('advisor', 'faculty', 'admin')
def export_csv():
    u = current_user()
    conds, args = role_scope(u)
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    rows = q('SELECT r.id, s.username, s.name, s.class_name, s.department, t.name type_name, '
             'r.start_date, r.end_date, r.days, r.reason, r.status, r.current_level, r.created_at '
             'FROM leave_requests r JOIN users s ON s.id=r.student_id '
             f'JOIN leave_types t ON t.id=r.leave_type_id {where} '
             'ORDER BY r.created_at DESC', args)

    buf = io.StringIO()
    buf.write('\ufeff')
    w = csv.writer(buf)
    w.writerow(['假單號', '學號', '姓名', '班級', '院系', '假別', '開始', '結束',
                '天數', '事由', '狀態', '目前層級', '提交時間'])
    for r in rows:
        # csv_safe：事由等自由文字若是 = + - @ 開頭，在 Excel 會被當公式執行
        w.writerow([csv_safe(v) if isinstance(v, str) else v
                    for v in (r['id'], r['username'], r['name'], r['class_name'], r['department'],
                              r['type_name'], r['start_date'], r['end_date'], r['days'],
                              r['reason'], STATUS_LABEL.get(r['status'], r['status']),
                              r['current_level'], r['created_at'])])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename=leave_{date.today():%Y%m%d}.csv'})


# --------------------------------------------------------------------------
# 管理後台
# --------------------------------------------------------------------------
def _render_admin_users(role='', onetime=None):
    """帳號管理頁的共用渲染（GET 與 POST 成功後的一次性呈現都用它）。"""
    sql = ('SELECT u.*, a.name advisor_name FROM users u '
           'LEFT JOIN users a ON a.id=u.advisor_id')
    args = []
    if role:
        sql += ' WHERE u.role=?'
        args.append(role)
    sql += ' ORDER BY u.role, u.username'
    advisors = q("SELECT id, name FROM users WHERE role='advisor' AND active=1 ORDER BY name")
    return render_template('admin_users.html', users=q(sql, args),
                           advisors=advisors, filter_role=role,
                           onetime_headline=onetime[0] if onetime else None,
                           onetime_secret=onetime[1] if onetime else None)


def _parse_advisor_id(raw):
    """正規化表單傳入的 advisor_id。

    回傳 (值, 錯誤訊息)。空值 → (None, None)；非整數、帳號不存在、
    非導師角色或已停用 → (None, 錯誤訊息)。少了這層檢查，
    亂填的 id 會撞外鍵約束直接 500。
    """
    if raw in (None, ''):
        return None, None
    try:
        aid = int(raw)
    except (TypeError, ValueError):
        return None, '導師帳號不合法。'
    row = q('SELECT id, role, active FROM users WHERE id=?', (aid,), one=True)
    if not row or row['role'] != 'advisor' or not row['active']:
        return None, '導師帳號不存在、非導師角色或已停用。'
    return aid, None


@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    return _render_admin_users(request.args.get('role', ''))


@app.route('/admin/users/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_user():
    username = request.form.get('username', '').strip()
    name = request.form.get('name', '').strip()
    role = request.form.get('role', 'student')
    department = request.form.get('department', '').strip()
    class_name = request.form.get('class_name', '').strip()
    email = request.form.get('email', '').strip()
    advisor_id, adv_err = _parse_advisor_id(request.form.get('advisor_id'))
    if adv_err:
        flash(adv_err, 'error')
        return redirect(url_for('admin_users'))
    if email and not valid_email(email):
        flash('Email 格式不合法。', 'error')
        return redirect(url_for('admin_users'))
    # 未填密碼時自動產生一組必定通過策略的隨機臨時密碼
    password = request.form.get('password', '').strip() or temp_password()
    err = validate_password(password, username)
    if err:
        flash(err, 'error')
        return redirect(url_for('admin_users'))

    if not username or not name:
        flash('帳號與姓名必填。', 'error')
        return redirect(url_for('admin_users'))
    if not valid_username(username):
        flash('帳號格式不合法（1–50 字元，僅字母、數字與 . _ - @）。', 'error')
        return redirect(url_for('admin_users'))
    if not valid_role(role):
        flash('角色不合法（限 student / advisor / faculty / admin）。', 'error')
        return redirect(url_for('admin_users'))
    if q('SELECT id FROM users WHERE username=?', (username,), one=True):
        flash(f'帳號 {username} 已存在。', 'error')
        return redirect(url_for('admin_users'))
    execute('INSERT INTO users (username, password_hash, name, role, department, '
            'class_name, email, advisor_id) VALUES (?,?,?,?,?,?,?,?)',
            (username, generate_password_hash(password), name, role,
             department, class_name, email, advisor_id))
    log_action(None, current_user()['id'], 'add_user', f'新增帳號 {username}（{role}）')
    # 明文密碼不進 flash：flash 存放在 session cookie 裡，而 cookie 只是簽名並非加密，
    # 任何拿到 cookie 的人都能讀出密碼，且它會一直待到下次請求被消費。
    # 改為僅在本次回應中呈現一次，任何地方都不留持久痕跡。
    return _render_admin_users(onetime=(f'已建立帳號 {username}。', password))


def toggle_user_error(me, target):
    """停用／啟用帳號的前置保護，回傳錯誤訊息或 None。

    - 不可停用目前登入中的自己；
    - 不可停用最後一位在職管理員（正常流程下登入者本身就是管理員，
      這條是錦上添花的深度防禦，防止腳本／未來角色改動造成鎖死）。
    """
    if target['id'] == me['id']:
        return '不可停用目前登入中的自己。'
    if target['role'] == 'admin' and target['active'] == 1:
        others = q("SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1 AND id<>?",
                   (target['id'],), one=True)['c']
        if others == 0:
            return '系統至少需保留一位在職管理員。'
    return None


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_user(uid):
    """停用／啟用帳號（含防呆保護，見 toggle_user_error）。"""
    me = current_user()
    target = q('SELECT id, username, role, active FROM users WHERE id=?', (uid,), one=True)
    if not target:
        flash('找不到該帳號。', 'error')
        return redirect(url_for('admin_users'))
    err = toggle_user_error(me, target)
    if err:
        flash(err, 'error')
        return redirect(url_for('admin_users'))
    new_active = 0 if target['active'] == 1 else 1
    execute('UPDATE users SET active=? WHERE id=?', (new_active, uid))
    log_action(None, me['id'], 'toggle_user',
               f"{'停用' if new_active == 0 else '啟用'} {target['username']}")
    flash(f"已{'停用' if new_active == 0 else '啟用'} {target['username']}。", 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/reset-password', methods=['POST'])
@login_required
@role_required('admin')
def admin_reset_password(uid):
    """管理員重設密碼。未填寫時自動產生一組符合強度策略的隨機臨時密碼。"""
    target = q('SELECT id, username FROM users WHERE id=?', (uid,), one=True)
    if not target:
        flash('找不到該帳號。', 'error')
        return redirect(url_for('admin_users'))
    new = request.form.get('password', '').strip() or temp_password()
    err = validate_password(new, target['username'])
    if err:
        flash(err, 'error')
        return redirect(url_for('admin_users'))
    # 更新密碼的同時刷新 pwd_changed_at：該帳號所有舊 session 立即作廢
    # （session 是簽名 cookie，不靠服務端黑名單，只能以時間戳記檢驗）
    execute('UPDATE users SET password_hash=?, pwd_changed_at=? WHERE id=?',
            (generate_password_hash(new), _now_str(), uid))
    log_action(None, current_user()['id'], 'reset_password', f"重設 {target['username']} 的密碼")
    # 同新增帳號：明文密碼只在本次回應中呈現一次，不進 session cookie
    return _render_admin_users(onetime=(f'已重設 {target["username"]} 的密碼。', new))


@app.route('/admin/users/<int:uid>/edit', methods=['POST'])
@login_required
@role_required('admin')
def admin_edit_user(uid):
    """調整姓名、院系、班級與導師歸屬（導師異動時最常需要）。"""
    target = q('SELECT id FROM users WHERE id=?', (uid,), one=True)
    if not target:
        flash('找不到該帳號。', 'error')
        return redirect(url_for('admin_users'))
    name = request.form.get('name', '').strip()
    if not name:
        flash('姓名不可為空。', 'error')
        return redirect(url_for('admin_users'))
    email = request.form.get('email', '').strip()
    if email and not valid_email(email):
        flash('Email 格式不合法。', 'error')
        return redirect(url_for('admin_users'))
    advisor_id, adv_err = _parse_advisor_id(request.form.get('advisor_id'))
    if adv_err:
        flash(adv_err, 'error')
        return redirect(url_for('admin_users'))
    execute('UPDATE users SET name=?, department=?, class_name=?, email=?, advisor_id=? '
            'WHERE id=?',
            (name,
             request.form.get('department', '').strip(),
             request.form.get('class_name', '').strip(),
             email,
             advisor_id, uid))
    log_action(None, current_user()['id'], 'edit_user', f'修改帳號 #{uid} 資料')
    flash('帳號資料已更新。', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/types')
@login_required
@role_required('admin')
def admin_types():
    return render_template('admin_types.html',
                           types=q('SELECT * FROM leave_types ORDER BY id'))


@app.route('/admin/types/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_type():
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    if not code or not name:
        flash('代號與名稱必填。', 'error')
        return redirect(url_for('admin_types'))
    execute('INSERT INTO leave_types (code, name, requires_attachment, needs_faculty) '
            'VALUES (?,?,?,?)',
            (code, name, 1 if request.form.get('requires_attachment') else 0,
             1 if request.form.get('needs_faculty') else 0))
    flash(f'已新增假別「{name}」。', 'success')
    return redirect(url_for('admin_types'))


@app.route('/healthz')
def healthz():
    """健康檢查（無需登入）：供負載均衡 / 容器探測 / CI 使用。"""
    try:
        q('SELECT 1')
        q('SELECT COUNT(*) c FROM users')      # schema 完整性：核心表可查
        return jsonify({'status': 'ok', 'db': 'up'})
    except Exception as exc:  # noqa: BLE001 — 探針只需回報狀態，不吐堆疊
        return jsonify({'status': 'error', 'db': str(exc)}), 503


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    """附件僅限本人、該生的導師、同院系審批人與管理員下載。

    病假證明屬個人敏感資料，僅「已登入」不足以構成授權。
    """
    u = current_user()
    req = q('SELECT r.id, r.student_id, s.advisor_id, s.department '
            'FROM leave_requests r JOIN users s ON s.id=r.student_id '
            'WHERE r.attachment=?', (filename,), one=True)
    if not req:
        flash('找不到該附件。', 'error')
        return redirect(url_for('dashboard'))
    allowed = (
        u['role'] == 'admin'
        or u['id'] == req['student_id']
        or (u['role'] == 'advisor' and u['id'] == req['advisor_id'])
        or (u['role'] == 'faculty' and u['department'] == req['department'])
    )
    if not allowed:
        flash('你沒有權限存取該檔案。', 'error')
        return redirect(url_for('dashboard'))
    return send_from_directory(UPLOAD_DIR, filename)


# --------------------------------------------------------------------------
# 種子資料
# --------------------------------------------------------------------------
def seed():
    if q('SELECT COUNT(*) c FROM users', one=True)['c']:
        return False
    # 示範統一密碼。刻意不用 Pass@123：它已列進 COMMON_PASSWORDS 擋鎖，
    # 種子資料雖然繞過 validate_password 直寫，但演示時「預設密碼本身違反
    # 自家策略」會讓人困惑，也給了使用者一個改不回去的錯誤示範。
    pw = generate_password_hash('Demo@2026')
    dept = '人工智能學域'

    admin = execute('INSERT INTO users (username, password_hash, name, role, department) '
                    'VALUES (?,?,?,?,?)', ('admin', pw, '系統管理員', 'admin', '教務處'))
    adv1 = execute('INSERT INTO users (username, password_hash, name, role, department) '
                   'VALUES (?,?,?,?,?)', ('T001', pw, '陳導師', 'advisor', dept))
    adv2 = execute('INSERT INTO users (username, password_hash, name, role, department) '
                   'VALUES (?,?,?,?,?)', ('T002', pw, '林導師', 'advisor', dept))
    fac1 = execute('INSERT INTO users (username, password_hash, name, role, department) '
                   'VALUES (?,?,?,?,?)', ('F001', pw, '王院辦', 'faculty', dept))

    students = [
        ('2024001', '李政霖', 'AI-1班', adv1),
        ('2024002', '張子謙', 'AI-1班', adv1),
        ('2024003', '黃曉嵐', 'AI-1班', adv1),
        ('2024004', '吳佩珊', 'AI-2班', adv2),
        ('2024005', '陳俊宏', 'AI-2班', adv2),
    ]
    for no, nm, cls, adv in students:
        execute('INSERT INTO users (username, password_hash, name, role, department, '
                'class_name, advisor_id, email) VALUES (?,?,?,?,?,?,?,?)',
                (no, pw, nm, 'student', dept, cls, adv, f'{no}@example.edu'))

    for code, nm, att, nf in [
            ('personal', '事假', 0, 0),
            ('sick',     '病假', 1, 0),
            ('official', '公假', 1, 1),
            ('bereave',  '喪假', 0, 1),
            ('other',    '其他', 0, 0)]:
        execute('INSERT INTO leave_types (code, name, requires_attachment, needs_faculty) '
                'VALUES (?,?,?,?)', (code, nm, att, nf))

    # 兩張示範假單
    today = date.today()
    sid = q("SELECT id FROM users WHERE username='2024001'", one=True)['id']
    rid = execute('INSERT INTO leave_requests (student_id, leave_type_id, start_date, end_date, '
                  'start_period, end_period, days, reason, contact_phone, status, '
                  'current_level, required_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (sid, 1, today.isoformat(), today.isoformat(), 'AM', 'PM', 1.0,
                   '參加校外學術競賽', '13800000001', 'pending', 1, 1))
    execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 1, 'pending'))
    notify(adv1, rid, '李政霖 提交了 1.0 天事假，待你審批。')

    sid2 = q("SELECT id FROM users WHERE username='2024002'", one=True)['id']
    rid2 = execute('INSERT INTO leave_requests (student_id, leave_type_id, start_date, end_date, '
                   'start_period, end_period, days, reason, contact_phone, status, '
                   'current_level, required_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (sid2, 2, today.isoformat(), (today + timedelta(days=5)).isoformat(),
                    'AM', 'PM', 6.0, '住院治療，附診斷證明', '13800000002', 'pending', 2, 2))
    execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid2, 1, 'pending'))
    execute('INSERT INTO approvals (request_id, level, approver_id, action, comment, acted_at) '
            'VALUES (?,?,?,?,?,?)', (rid2, 1, adv1, 'approve', '情況屬實，同意轉院系。',
                                     datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid2, 2, 'pending'))
    notify(fac1, rid2, '張子謙 的 6.0 天病假待院系核複。')
    seed_timetable()
    return True


@app.cli.command('init-db')
def init_db_command():
    init_db()
    seed_timetable()
    print('資料庫已初始化。')


if __name__ == '__main__':
    with app.app_context():
        init_db()
        created = seed()
        if created:
            print('已寫入示範資料。')
        seed_timetable()
    app.run(host='127.0.0.1', port=5055, debug=True)
