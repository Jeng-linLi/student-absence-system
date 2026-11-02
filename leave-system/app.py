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
import urllib.error
import time
import secrets
import shutil
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, jsonify, send_from_directory, Response, abort,
                   make_response, g, has_request_context, stream_with_context)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import smtplib
from email.message import EmailMessage

import click

from db import (BASE_DIR, DB_PATH, UPLOAD_DIR, get_db, close_db,  # noqa: F401
                q, execute, execute_guard, transaction, init_db)
from utils import (FACULTY_THRESHOLD_DAYS, ALLOWED_EXT,
                   MAX_BACKDATE_DAYS, MAX_SINGLE_DAYS, MAX_ATTACHMENT_MB,
                   MAX_REASON_LEN, MAX_COMMENT_LEN, MAX_PHONE_LEN,
                   MIN_PASSWORD_LEN, MAX_PASSWORD_LEN,
                   LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SEC,
                   ACADEMIC_YEAR_START_MONTH, SESSION_IDLE_MINUTES,
                   APP_VERSION, LEAVE_STATUSES, MAX_TYPE_NAME_LEN,
                   MAX_BULK_DECIDE,
                   parse_date, calc_days, required_level_for,
                   academic_year_start, allowed_file, is_hhmm,
                   validate_password, temp_password, csv_safe,
                   valid_role, valid_username, valid_email, valid_phone,
                   sniff_allowed_type, valid_type_code)
from calendar_import import (parse_calendar_text, apply_calendar,
                             load_preset, preset_for_today, PRESETS)
from timetable_import import (parse_timetable_csv, apply_timetable,
                             timetable_coverage, pad_time, MAX_IMPORT_ROWS)
from sso import sso_configured, sso_settings, authorize_url, exchange_code, \
    fetch_userinfo, resolve_user

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

# 單一登入（SSO / OIDC，A2·v1.8.0）：把環境變數載入 app.config，
# 既能被 sso.py 讀到，也能在測試時由 app.config 直接覆寫。
for _k in ('SSO_CLIENT_ID', 'SSO_CLIENT_SECRET', 'SSO_AUTHORIZE_URL',
           'SSO_TOKEN_URL', 'SSO_USERINFO_URL', 'SSO_REDIRECT_URI',
           'SSO_SCOPES', 'SSO_USERNAME_CLAIM', 'SSO_ROLE_CLAIM',
           'SSO_DEFAULT_ROLE', 'SSO_PROVISION', 'SSO_AUTO_LINK_EMAIL'):
    app.config.setdefault(_k, os.environ.get(_k, ''))


@app.teardown_appcontext
def _close_db(e=None):
    close_db(e)


WEEKDAY_LABELS = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']

# 示範課表（demo 學生共用）。正式環境應由 SIS / LMS 同步，而非硬編碼。
# v1.7.0：寫入時標 source='demo'。它是「讓受影響課程功能看得到東西」的
# 展示用資料，不是任何一個真實學生的課表——管理後台會據此警示覆蓋率。
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


def is_school_off(d):
    """是否非上課日（v1.4.0 · C3）：週六日，或校曆上的假日。

    假日請假本來就不會缺課——affected_courses_for() 若照樣列出
    「受影響課程」，審批人會誤以為學生缺了兩節課。

    v1.5.0：補課日（makeup_days）是例外——那天就算落在週六，學校照常
    上（別的星期幾的）課，請假一樣算缺課。
    """
    iso = d.isoformat()
    if q('SELECT 1 FROM makeup_days WHERE day=?', (iso,), one=True):
        return False
    if d.weekday() >= 5:
        return True
    return bool(q('SELECT 1 FROM holidays WHERE day=?', (iso,), one=True))


def affected_courses_for(student_id, start_date, end_date,
                        start_period='AM', end_period='PM'):
    """依學生課表，算出請假區間內受影響的課程（按半天粒度）。

    v1.4.0：跳過非上課日（週末與校曆假日）。假日集合一次取出，
    不在日期迴圈裡逐日查庫。
    """
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
    off_days = {r['day'] for r in q('SELECT day FROM holidays')}
    # v1.5.0：補課日 → 當天要上的是「被補的那個星期幾」的課
    makeup = {r['day']: r['cover_weekday']
              for r in q('SELECT day, cover_weekday FROM makeup_days')}
    out = []
    d = s
    while d <= e:
        iso = d.isoformat()
        if iso in makeup:
            # 補課日優先：即使是週六／假日，當天照常上課
            wd = makeup[iso]
        elif d.weekday() >= 5 or iso in off_days:
            # 週末與校曆假日：不算缺課，直接跳過
            d += timedelta(days=1)
            continue
        else:
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
    """寫入示範課表（冪等：已存在則跳過）。正式環境請由 SIS / LMS 同步。

    v1.7.0：這裡寫的每一列都標 source='demo'——它是整個示範環境的起點，
    也是「課表還沒真實化」的證據。匯入真實課表後這些列會被取代，
    管理後台的覆蓋率警示隨之解除。
    """
    if q('SELECT 1 FROM timetable LIMIT 1'):
        return
    for sid in q('SELECT id FROM users WHERE role=?', ('student',)):
        for wd, st, et, code, kind, room, note in DEMO_TIMETABLE:
            execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
                    "course_code, kind, room, note, source) VALUES (?,?,?,?,?,?,?,?,'demo')",
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
        # timeout 不可省略：SMTP 伺服器不可達（防火牆丟包）時，無逾時的
        # SMTP() 會無限期阻塞，把整個 worker 拖死——每個會觸發 notify 的
        # 請求都跟著掛（v1.1.0）。可用 LEAVE_SMTP_TIMEOUT 調整。
        timeout = int(os.environ.get('LEAVE_SMTP_TIMEOUT', '10'))
        with smtplib.SMTP(os.environ['LEAVE_SMTP_HOST'],
                          int(os.environ.get('LEAVE_SMTP_PORT', '587')),
                          timeout=timeout) as s:
            if os.environ.get('LEAVE_SMTP_TLS', '1') == '1':
                s.starttls()
            if os.environ.get('LEAVE_SMTP_USER'):
                s.login(os.environ['LEAVE_SMTP_USER'], os.environ.get('LEAVE_SMTP_PASS', ''))
            s.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — 通知通道失敗不應影響主流程
        print(f'[通知] 郵件發送失敗（已忽略，不影響請假流程）：{exc}')


def notify(user_id, request_id, message):
    """站內通知永遠寫入；若已設定 SMTP，再額外寄一封 Email（失敗靜默）。

    Email 不在呼叫點同步寄出，而是進佇列（見 _queue_email）——
    notify 幾乎都在交易區塊內被呼叫，同步寄信會讓 SQLite 寫入鎖
    在整個 SMTP 連線期間被持有。
    """
    if user_id:
        execute('INSERT INTO notifications (user_id, request_id, message) VALUES (?,?,?)',
                (user_id, request_id, message))
        if _smtp_configured():
            u = q('SELECT email FROM users WHERE id=?', (user_id,), one=True)
            if u and u['email']:
                _queue_email(u['email'], message)


def _queue_email(to_addr, message):
    """把通知郵件放進佇列，延到交易提交後再寄（v1.2.0）。

    原因：寄信是網路 I/O，SMTP 連線最長可達 LEAVE_SMTP_TIMEOUT 秒
    （v1.1.0 加的逾時）。舊版在交易內同步寄出，等於讓 SQLite 的
    寫入鎖整個寄信過程都被持有——任何併發的審批／提交都會被阻塞，
    甚至撞上 database is locked。現在交易期間只入佇列：

    - 請求週期內：由 atomic() 在最外層交易成功提交後寄出；
    - 非請求週期（CLI、seed）：沒有 after_request 會來收割，立即寄出。
    """
    if getattr(g, '_pending_emails', None) is None:
        g._pending_emails = []
    g._pending_emails.append((to_addr, message))
    # 非請求週期（CLI、seed）且不在交易中：沒有 after_request 會來收割、
    # 也沒有 atomic() 會在提交後寄出——立即寄出。若在交易中（縱然沒有
    # 請求週期），是否寄出交給 atomic() 依提交／回滾決定。
    if not has_request_context() and not getattr(g, '_tx_depth', 0):
        _flush_emails()


def _flush_emails():
    """寄出佇列中的郵件並清空。send_email 本身失敗靜默，這裡不補catch。"""
    pending = getattr(g, '_pending_emails', None)
    if not pending:
        return
    g._pending_emails = []
    for to_addr, message in pending:
        send_email(to_addr, '線上請假系統通知', message)


@contextmanager
def atomic():
    """transaction() ＋ 通知郵件佇列語義（v1.2.0）。

    notify() 在交易期間只把郵件放進 g._pending_emails；最外層交易
    成功提交後才實際寄出。若交易擲出例外而回滾，把在此交易期間
    入佇列的郵件一併作廢——站內信的寫入隨交易消失，郵件不該
    單獨寄到使用者手上。
    """
    mark = len(getattr(g, '_pending_emails', None) or [])
    try:
        with transaction():
            yield
    except Exception:
        queued = getattr(g, '_pending_emails', None)
        if queued:
            del queued[mark:]
        raise
    _flush_emails()


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


def _safe_referrer():
    """request.referrer 是攻擊者可控的完整 URL——CSRF 失敗、413 之類的
    處理若直接 redirect(referrer)，使用者會被送往外部網站（open redirect，
    常用於釣魚：頁面長得跟真系統一樣）。只有 Host 與本站相同才沿用，
    其餘一律回dashboard。"""
    ref = request.referrer
    if ref:
        try:
            parts = urlsplit(ref)
        except ValueError:
            parts = None
        if parts and parts.scheme in ('http', 'https') and parts.netloc == request.host:
            return ref
    return url_for('dashboard')


@app.errorhandler(400)
def bad_request(e):
    flash(getattr(e, 'description', '請求無效。'), 'error')
    return redirect(_safe_referrer())


@app.errorhandler(413)
def too_large(e):
    """請求體過大（附件超過 MAX_ATTACHMENT_MB）：Werkzeug 預設會回一篇
    白底錯誤頁，改成 flash 提示並導回原頁面（v1.0.0）。"""
    flash(f'上傳內容過大（上限 {MAX_ATTACHMENT_MB} MB），請壓縮或改傳較小檔案。', 'error')
    return redirect(_safe_referrer())


@app.after_request
def flush_pending_emails(resp):
    """安全網：交易外入佇列的通知郵件在回應送出前寄出。

    正常路徑由 atomic() 在交易提交後負責；這裡覆蓋「notify 於
    交易外被呼叫」的少數路徑（例如未包 atomic 的 CLI 流程）。
    """
    _flush_emails()
    return resp


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


# --------------------------------------------------------------------------
# 委託審批（B4 · v1.4.0）
# --------------------------------------------------------------------------
def active_delegations(delegate_id, level=None, on_date=None):
    """在指定日期生效、委託給 delegate_id 的委託記錄。

    只算「今天落在 [start_date, end_date] 內」的——過期的委託自動失效，
    不需要額外清理任務（歷史記錄留著剛好當稽核軌跡）。
    """
    day = (on_date or date.today()).isoformat()
    sql = ('SELECT d.*, u.name delegator_name, u.department delegator_dept, '
           'u.role delegator_role '
           'FROM delegations d JOIN users u ON u.id=d.delegator_id '
           "WHERE d.delegate_id=? AND u.active=1 "
           "AND date(?) BETWEEN date(d.start_date) AND date(d.end_date)")
    args = [delegate_id, day]
    if level is not None:
        sql += ' AND d.level=?'
        args.append(level)
    return q(sql + ' ORDER BY d.level, d.start_date', args)


def approvable_advisor_ids(u):
    """導師這一級能審批的「導師 id」集合：自己的 id ＋ 委託給我的導師。"""
    if u['role'] != 'advisor':
        return set()
    ids = {u['id']}
    for d in active_delegations(u['id'], 1):
        if d['delegator_role'] == 'advisor':
            ids.add(d['delegator_id'])
    return ids


def approvable_departments(u):
    """院系這一級能審批的院系集合：自己的院系 ＋ 委託給我的院系教師所屬院系。"""
    if u['role'] != 'faculty':
        return set()
    depts = {u['department']} if u['department'] else set()
    for d in active_delegations(u['id'], 2):
        if d['delegator_role'] == 'faculty' and d['delegator_dept']:
            depts.add(d['delegator_dept'])
    return depts


def todo_scope(u):
    """待辦查詢的範圍條件。

    回傳 (level, cond_sql, args)；level=None 表示管理員看全部。
    v1.4.0：把「代理人視角」併進來——被委託的導師／院系教師，
    待辦清單與數字都要跟原審批人一致，否則代理了卻看不到單。
    """
    if u['role'] == 'advisor':
        ids = sorted(approvable_advisor_ids(u))
        ph = ','.join('?' * len(ids))
        return 1, f's.advisor_id IN ({ph})', ids
    if u['role'] == 'faculty':
        depts = sorted(approvable_departments(u))
        if not depts:
            return 2, '0', []
        ph = ','.join('?' * len(depts))
        return 2, f's.department IN ({ph})', depts
    return None, '', []


def can_approve_request(u, req):
    """u 現在能否審批 req（含代理人身分）。req 需有 status/current_level/
    department/advisor_id（學生的）。"""
    if not req or req['status'] != 'pending':
        return False
    if u['role'] == 'admin':
        return True
    level = req['current_level']
    if level == 1:
        return u['role'] == 'advisor' and req['advisor_id'] in approvable_advisor_ids(u)
    if level == 2:
        return u['role'] == 'faculty' and req['department'] in approvable_departments(u)
    return False


def can_view_request(u, req):
    """檢視權限：比審批權限寬一點——曾審批過的人即使學生換了導師，
    也應該看得到自己經手過的單（否則稽核時查不到自己做了什麼）。"""
    if u['role'] == 'admin':
        return True
    if u['role'] == 'student':
        return req['student_id'] == u['id']
    # 導師／院系依「範圍」看得到自己轄下所有假單（含非當前審批層級的，
    # 供督導與稽核），不限於當下待審的那一級——否則院系看不到還停在
    # 導師層級的本院系假單，與 v1.3.0 行為一致。
    if u['role'] == 'advisor':
        if req['advisor_id'] in approvable_advisor_ids(u):
            return True
    if u['role'] == 'faculty':
        if req['department'] in approvable_departments(u):
            return True
    # 曾審批過的人也看得到自己經手過的單（即使學生後來換了導師）
    return bool(q('SELECT 1 FROM approvals WHERE request_id=? AND approver_id=?',
                  (req['id'], u['id']), one=True))


def notify_approvers(level, department, advisor_id, rid, message, on_date=None):
    """通知某一級的所有實際審批人（含代理人）。

    v1.4.0：委託生效期間，代理人也是「實際審批人」——只通知原審批人
    的話，代理人待辦裡有單卻收不到任何提醒，等於白委託。
    """
    day = (on_date or date.today()).isoformat()
    targets = set()
    if level == 1:
        if advisor_id:
            targets.add(advisor_id)
            for d in q('SELECT d.delegate_id FROM delegations d '
                       'JOIN users u ON u.id=d.delegator_id '
                       'WHERE d.level=1 AND d.delegator_id=? AND u.active=1 '
                       'AND date(?) BETWEEN date(d.start_date) AND date(d.end_date)',
                       (advisor_id, day)):
                targets.add(d['delegate_id'])
    else:
        for f in q("SELECT id FROM users WHERE role='faculty' AND active=1 AND department=?",
                   (department,)):
            targets.add(f['id'])
        for d in q("SELECT d.delegate_id FROM delegations d "
                   'JOIN users u ON u.id=d.delegator_id '
                   "WHERE d.level=2 AND u.role='faculty' AND u.active=1 AND u.department=? "
                   'AND date(?) BETWEEN date(d.start_date) AND date(d.end_date)',
                   (department, day)):
            targets.add(d['delegate_id'])
    for tid in targets:
        notify(tid, rid, message)


# --------------------------------------------------------------------------
# 學期與額度（C2 · v1.4.0）
# --------------------------------------------------------------------------
def current_term():
    """目前學期：is_current 旗標 → 日期落在區間內 → 最近結束的學期。"""
    t = q('SELECT * FROM terms WHERE is_current=1 ORDER BY start_date DESC LIMIT 1', one=True)
    if t:
        return t
    today = date.today().isoformat()
    t = q('SELECT * FROM terms WHERE date(?) BETWEEN date(start_date) AND date(end_date) '
          'ORDER BY start_date DESC LIMIT 1', (today,), one=True)
    if t:
        return t
    return q('SELECT * FROM terms ORDER BY end_date DESC LIMIT 1', one=True)


def term_for_date(day):
    """某一天屬於哪個學期（以請假起始日歸屬，避免跨學期假單重複計入）。"""
    iso = day.isoformat() if isinstance(day, date) else str(day)
    return q('SELECT * FROM terms WHERE date(?) BETWEEN date(start_date) AND date(end_date) '
             'ORDER BY start_date DESC LIMIT 1', (iso,), one=True)


def quota_limit(leave_type_id, term_id):
    """該學期、該假別的額度上限；未設定回傳 None（視為不限）。"""
    row = q("SELECT max_days FROM leave_quotas WHERE term_id=? AND leave_type_id=? "
            "AND scope='all' ORDER BY id LIMIT 1", (term_id, leave_type_id), one=True)
    return row['max_days'] if row else None


def quota_used(student_id, leave_type_id, term):
    """該學期已佔用的天數：待審＋已核准都算——否則學生可以連續提交
    十張待審假單繞過額度。"""
    return q("SELECT COALESCE(SUM(days),0) s FROM leave_requests "
             "WHERE student_id=? AND leave_type_id=? "
             "AND status IN ('pending','approved') "
             "AND date(start_date) BETWEEN date(?) AND date(?)",
             (student_id, leave_type_id, term['start_date'], term['end_date']),
             one=True)['s']


def quota_exceeded(student_id, ltype, start_date, days):
    """提交前的額度校驗；回傳錯誤訊息或 None。"""
    term = term_for_date(start_date)
    if not term:
        return None
    limit = quota_limit(ltype['id'], term['id'])
    if limit is None:
        return None
    used = quota_used(student_id, ltype['id'], term)
    if used + days > limit + 1e-9:
        return (f"本學期（{term['label']}）「{ltype['name']}」額度 {limit:g} 天，"
                f"已用 {used:g} 天，本次 {days:g} 天將超出上限。")
    return None


def student_quota_board(student_id):
    """學生儀表板用：本學期每個「有設額度」的假別用量。"""
    term = current_term()
    if not term:
        return term, []
    rows = q('SELECT qt.leave_type_id, qt.max_days, t.name type_name, '
             "COALESCE((SELECT SUM(r.days) FROM leave_requests r "
             "          WHERE r.student_id=? AND r.leave_type_id=qt.leave_type_id "
             "          AND r.status IN ('pending','approved') "
             "          AND date(r.start_date) BETWEEN date(?) AND date(?)), 0) used "
             'FROM leave_quotas qt JOIN leave_types t ON t.id=qt.leave_type_id '
             "WHERE qt.term_id=? AND qt.scope='all' ORDER BY t.id",
             (student_id, term['start_date'], term['end_date'], term['id']))
    return term, rows


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
    """回傳目前登入者；session 閒置逾時或登入後密碼被改過則一律視為未登入。

    注意：不可在 flask.g 上快取——g 的生命週期是 app context，而同一 app
    context 可能服務多個請求（測試或 CLI 常見），快取會把上一個請求的使用者
    身份帶進下一個請求（v1.0.0 實測踩坑，已回退）。
    """
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
                sso_enabled=sso_configured(),
                min_date=(date.today() - timedelta(days=MAX_BACKDATE_DAYS)).isoformat(),
                max_date=(date.today() + timedelta(days=365)).isoformat(),
                MAX_SINGLE_DAYS=MAX_SINGLE_DAYS, MAX_BACKDATE_DAYS=MAX_BACKDATE_DAYS,
                MAX_REASON_LEN=MAX_REASON_LEN, MAX_COMMENT_LEN=MAX_COMMENT_LEN,
                MAX_PHONE_LEN=MAX_PHONE_LEN,
                MAX_BULK_DECIDE=MAX_BULK_DECIDE)


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


def _establish_session(user):
    """建立登入 session（本地登入與 SSO 回呼共用）。

    一律先 clear 再寫，避免 session fixation；並寫入 auth_at 讓「登入後改密碼
    舊 session 即時作廢」的機制對 SSO 登入也成立。
    """
    session.clear()
    session['uid'] = user['id']
    session['csrf_token'] = secrets.token_hex(32)
    session['auth_at'] = _now_str()
    session['last_seen'] = session['auth_at']


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
            resp = make_response(render_template('login.html'), 429)
            resp.headers['Retry-After'] = str(LOGIN_WINDOW_SEC)
            return resp
        user = q('SELECT * FROM users WHERE username=? AND active=1', (username,), one=True)
        if user and check_password_hash(user['password_hash'], password):
            login_succeeded(key)
            login_succeeded(user_key)
            _establish_session(user)
            return redirect(url_for('dashboard'))
        if not user:
            # 帳號不存在也跑一次雜湊比對，拉平與「帳號存在但密碼錯誤」的時間差
            check_password_hash(_DUMMY_PASSWORD_HASH, password)
        login_failed(key)
        login_failed(user_key)
        flash('帳號或密碼錯誤。', 'error')
    return render_template('login.html')


# 單一登入（SSO / OIDC，A2·v1.8.0）：身分中介層插在 login() 之前。
# 本地帳號密碼登入一律保留為災難備援——SSO 掛掉時管理員仍能進系統。
@app.route('/login/sso')
def login_sso():
    if not sso_configured():
        flash('單一登入（SSO）尚未啟用。', 'error')
        return redirect(url_for('login'))
    # state 防 CSRF／授權碼注入；綁在 session 上，回呼時比對
    state = secrets.token_urlsafe(24)
    session['sso_state'] = state
    return redirect(authorize_url(state))


@app.route('/callback')
def sso_callback():
    if not sso_configured():
        return redirect(url_for('login'))
    # ① state 比對：失敗直接踢回登入，不給任何錯誤細節
    state = request.args.get('state', '')
    if not state or state != session.pop('sso_state', None):
        flash('SSO 狀態驗證失敗，請重新登入。', 'error')
        return redirect(url_for('login'))
    code = request.args.get('code')
    if not code:
        flash('SSO 授權碼缺失。', 'error')
        return redirect(url_for('login'))
    # ② 兌換授權碼 → userinfo；網路錯誤不該把頁面炸掉，退回本地登入
    try:
        token = exchange_code(code)
        claims = fetch_userinfo(token['access_token'])
    except (urllib.error.URLError, ValueError, KeyError, Exception):  # noqa: BLE001
        flash('SSO 身分驗證失敗，請稍後再試或改用本地帳號。', 'error')
        return redirect(url_for('login'))
    # ③ 對映到本地使用者；對應不到則退回（不建立來歷不明的帳號）
    user, err = resolve_user(claims)
    if not user:
        flash('SSO 帳號無法對應到本系統使用者。', 'error')
        return redirect(url_for('login'))
    _establish_session(user)
    return redirect(url_for('dashboard'))


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
    """待當前用戶審批的假單數量（v1.4.0：含代理人身分）。"""
    level, cond, args = todo_scope(u)
    if level is None:
        return q("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'", one=True)['c']
    if cond == '0':                       # 院系帳號沒填院系：沒有可審的範圍
        return 0
    return q('SELECT COUNT(*) c FROM leave_requests r JOIN users s ON s.id=r.student_id '
             f"WHERE r.status='pending' AND r.current_level=? AND {cond}",
             [level] + list(args), one=True)['c']


# --------------------------------------------------------------------------
# 儀表板
# --------------------------------------------------------------------------
@app.route('/')
@login_required
def dashboard():
    u = current_user()
    ctx = {'pending_mine': 0, 'todo': 0, 'recent': [], 'stats': {},
           'my_leave_days': 0, 'year_leave_days': 0, 'year_start': '',
           'term': None, 'quotas': [], 'term_days': 0}

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
        # v1.4.0：學期切分＋額度。學年數字看不出「這學期還剩多少額度」，
        # 學生要的是後者。
        term, quotas = student_quota_board(u['id'])
        ctx['term'] = term
        ctx['quotas'] = quotas
        ctx['term_days'] = q("SELECT COALESCE(SUM(days),0) s FROM leave_requests "
                             "WHERE student_id=? AND status='approved' "
                             "AND date(start_date) BETWEEN date(?) AND date(?)",
                             (u['id'], term['start_date'], term['end_date']),
                             one=True)['s'] if term else 0
        ctx['recent'] = q('SELECT r.*, t.name type_name FROM leave_requests r '
                          'JOIN leave_types t ON t.id=r.leave_type_id '
                          'WHERE r.student_id=? ORDER BY r.created_at DESC LIMIT 5', (u['id'],))
    else:
        ctx['todo'] = count_todo(u)
        ctx['recent'] = todo_requests(u, limit=5)
        # v1.0.0：彙總數字同樣套用範圍隔離——導師只看自己的學生、院系只看本院系、
        # 管理員看全部。此前不論角色一律顯示全系統統計，與 /stats、/export、
        # /approvals 的矛盾原則：聚合數字雖不含事由，仍能推測他人的請假規模。
        conds, sargs = role_scope(u)
        ctx['scope_label'] = {'advisor': '我的學生', 'faculty': '本院系',
                              'admin': '全校'}[u['role']]

        def _count(extra=''):
            w = list(conds)
            a = list(sargs)
            if extra:
                w.append(extra)
            sql = ('SELECT COUNT(*) c FROM leave_requests r '
                   'JOIN users s ON s.id=r.student_id'
                   + ((' WHERE ' + ' AND '.join(w)) if w else ''))
            return q(sql, a, one=True)['c']

        ctx['stats'] = {
            'total': _count(),
            'pending': _count("r.status='pending'"),
            'approved': _count("r.status='approved'"),
            'rejected': _count("r.status='rejected'"),
        }
    return render_template('dashboard.html', **ctx)


def todo_requests(u, limit=None, offset=0):
    """待當前用戶審批的假單。limit/offset 供分頁使用（v1.1.0：待辦清單
    此前一次載入全部待審假單，審批人學生一多頁面就無限變長）。

    v1.4.0：範圍改由 todo_scope() 決定，代理人的待辦與原審批人一致。
    """
    level, cond, args = todo_scope(u)
    base = ('SELECT r.*, t.name type_name, s.name student_name, s.class_name '
            'FROM leave_requests r '
            'JOIN leave_types t ON t.id=r.leave_type_id '
            'JOIN users s ON s.id=r.student_id ')
    if level is None:
        sql = base + "WHERE r.status='pending' ORDER BY r.created_at ASC"
        params = []
    elif cond == '0':
        return []
    else:
        sql = (base + f"WHERE r.status='pending' AND r.current_level=? AND {cond} "
               'ORDER BY r.created_at ASC')
        params = [level] + list(args)
    if limit:
        sql += ' LIMIT ? OFFSET ?'
        params += [int(limit), max(int(offset or 0), 0)]
    return q(sql, params)


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
# 課表（v1.7.0 · C1）：學生看自己的，審批人看轄下學生的
# --------------------------------------------------------------------------
def _timetable_by_student(sid):
    """取某位學生的週課表，按星期與時間排序（供模板直接分組）。"""
    return [dict(r, weekday_label=WEEKDAY_LABELS[r['weekday']]) for r in
            q('SELECT * FROM timetable WHERE student_id=? '
              'ORDER BY weekday, start_time', (sid,))]


def _can_view_timetable(u, student):
    """能否查看這位學生的課表。

    比照 can_view_request() 的範圍邏輯：管理員全部、導師自己的學生
    （含委託代理）、院系本院系。學生的課表跟假單一樣是個人資料，
    不是任何一個登入者都該看得到。
    """
    if not student or student['role'] != 'student':
        return False
    if u['role'] == 'admin':
        return True
    if u['role'] == 'advisor':
        return student['advisor_id'] in approvable_advisor_ids(u)
    if u['role'] == 'faculty':
        return student['department'] in approvable_departments(u)
    return False


def _visible_students(u):
    """u 有權查看課表的學生清單（管理員全部，其餘按範圍）。"""
    if u['role'] == 'admin':
        return q("SELECT id, username, name, class_name, department FROM users "
                 "WHERE role='student' AND active=1 ORDER BY username")
    if u['role'] == 'advisor':
        ids = sorted(approvable_advisor_ids(u))
        if not ids:
            return []
        ph = ','.join('?' * len(ids))
        return q('SELECT id, username, name, class_name, department FROM users '
                 f"WHERE role='student' AND active=1 AND advisor_id IN ({ph}) "
                 'ORDER BY username', ids)
    if u['role'] == 'faculty':
        depts = sorted(approvable_departments(u))
        if not depts:
            return []
        ph = ','.join('?' * len(depts))
        return q('SELECT id, username, name, class_name, department FROM users '
                 f"WHERE role='student' AND active=1 AND department IN ({ph}) "
                 'ORDER BY username', depts)
    return []


@app.route('/timetable')
@app.route('/timetable/<int:uid>')
@login_required
def timetable(uid=None):
    """週課表檢視。學生看自己的；導師／院系／管理員可指定學生。

    審批人看得到學生課表，「受影響課程」才有意義——否則核准時只看得到
    一串課程代碼，無法判斷這張假單到底會不會影響到關鍵的課。
    """
    u = current_user()
    if u['role'] == 'student':
        student = q('SELECT * FROM users WHERE id=?', (u['id'],), one=True)
    else:
        students = _visible_students(u)
        if uid:
            student = q('SELECT * FROM users WHERE id=?', (uid,), one=True)
            if not _can_view_timetable(u, student):
                flash('你沒有權限查看該學生的課表。', 'error')
                return redirect(url_for('timetable'))
        else:
            # 沒指定學生 → 給名單讓他挑（課表不是單一頁面能列完的東西）
            return render_template('timetable.html', students=students,
                                   sel=None, rows=[], by_day={},
                                   is_demo=False)
    days = [(i, WEEKDAY_LABELS[i]) for i in range(5)]
    rows = _timetable_by_student(student['id'])
    by_day = {}
    for r in rows:
        by_day.setdefault(r['weekday'], []).append(r)
    # 全是示範資料時明白講：這不是這位學生的真實課表
    is_demo = bool(rows) and all(r['source'] == 'demo' for r in rows)
    return render_template('timetable.html', students=students if u['role'] != 'student' else [],
                           sel=student, rows=rows, by_day=by_day, is_demo=is_demo,
                           days=days)


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
        if sp not in ('AM', 'PM') or ep not in ('AM', 'PM'):
            flash('上午／下午時段不合法。', 'error')
            return render_template('new_request.html', types=types, form=request.form)
        # 同日「下午→上午」是矛盾輸入：calc_days 只算 0.5 天，受影響課程卻按
        # 全天計，兩個口徑對不起來——乾脆在入口擋掉（v1.0.0）。
        if start_date == end_date and sp == 'PM' and ep == 'AM':
            flash('同一天請假時，結束時段不可早於開始時段。', 'error')
            return render_template('new_request.html', types=types, form=request.form)

        reason = request.form.get('reason', '').strip()
        phone = request.form.get('contact_phone', '').strip()
        # active=1：停用的假別不只在表單裡隱藏，直 POST 也提交不進來
        ltype = q('SELECT * FROM leave_types WHERE id=? AND active=1', (type_id,), one=True)
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
        elif not valid_phone(phone):
            err = ('聯絡電話格式不合法（僅能包含數字與 + ( ) - 空格；'
                   f'上限 {MAX_PHONE_LEN} 字）。')
        else:
            # 其餘校驗都通過才查資料庫（整個流程只查一次重疊）
            clashes = overlapping_requests(u['id'], start_date, end_date)
            if clashes:
                err = (f'你在此區間已有假單 #{clashes[0]["id"]}'
                       f'（{clashes[0]["start_date"]} ~ {clashes[0]["end_date"]}），'
                       '請先撤回或修改日期。')
            else:
                # v1.4.0：學期額度校驗（C2）。待審＋已核准都計入，
                # 否則連續提交多張待審單就能繞過上限。
                err = quota_exceeded(u['id'], ltype, start_date, days)
        if err:
            flash(err, 'error')
            return render_template('new_request.html', types=types, form=request.form)

        attachment = ''
        file = request.files.get('attachment')
        has_file = bool(file and file.filename)
        # v1.2.0：必須附檔但沒附——在落盤之前就擋掉。舊版把檔案先存進
        # uploads/ 才檢查，被擋下的檔案沒有資料庫紀錄與它對應，成了
        # 永遠沒人認領的孤兒（還帶著個資）。
        if ltype['requires_attachment'] and not has_file:
            flash(f"「{ltype['name']}」必須上傳證明文件。", 'error')
            return render_template('new_request.html', types=types, form=request.form)
        if has_file:
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
            # 原檔名過長時先截斷：secure_filename 不限制長度，組合後的檔名
            # 可能超過檔案系統 255 位元組上限，save() 直接 OSError 變 500（v1.0.0）
            raw_name = secure_filename(file.filename)
            stem, dot, ext = raw_name.rpartition('.')
            if len(stem) > 80:
                stem = stem[:80]
            safe_name = f'{stem}{dot}{ext}' if dot else stem
            fn = secure_filename(
                f"{u['username']}_{datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(6)}_{safe_name}")
            file.save(os.path.join(UPLOAD_DIR, fn))
            attachment = fn

        level = required_level_for(days, ltype)
        affected = affected_courses_for(u['id'], start_date.isoformat(),
                                        end_date.isoformat(), sp, ep)
        affected_json = json.dumps(affected, ensure_ascii=False)
        # 假單＋兩級審批記錄＋稽核＋通知要麼全部寫入、要麼全部不寫——
        # 逐句 commit 的話，中途失敗會留下「有假單沒有審批記錄」的孤兒。
        # atomic() 另負責在提交後才寄出通知郵件（不佔著寫入鎖寄信）。
        with atomic():
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
            # v1.4.0：通知該級所有實際審批人（導師＋其代理人）
            notify_approvers(1, u['department'], u['advisor_id'], rid,
                             f"{u['name']} 提交了 {days} 天{ltype['name']}，待你審批。")
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
            's.advisor_id, a.name advisor_name '
            'FROM leave_requests r '
            'JOIN leave_types t ON t.id=r.leave_type_id '
            'JOIN users s ON s.id=r.student_id '
            'LEFT JOIN users a ON a.id=s.advisor_id '
            'WHERE r.id=?', (rid,), one=True)
    if not req:
        flash('找不到該假單。', 'error')
        return redirect(url_for('dashboard'))

    # v0.9.1：導師只看自己的學生、院系只看本院系、管理員看全部。
    # v1.4.0：改由 can_view_request 統一判斷——代理審批人、以及曾審批過的
    # 人（即使學生後來換了導師）都要看得到，否則稽核時查不到自己經手的單。
    if not can_view_request(u, req):
        flash('你沒有權限查看該假單。', 'error')
        return redirect(url_for('dashboard'))

    approvals = q('SELECT ap.*, u.name approver_name, u.role approver_role '
                  'FROM approvals ap LEFT JOIN users u ON u.id=ap.approver_id '
                  'WHERE ap.request_id=? ORDER BY ap.level', (rid,))
    logs = q('SELECT l.*, u.name actor_name FROM audit_logs l '
             'LEFT JOIN users u ON u.id=l.actor_id WHERE l.request_id=? '
             'ORDER BY l.created_at DESC, l.id DESC', (rid,))

    # v1.4.0：含代理人身分（被委託的導師／院系教師也能審）
    can_approve = can_approve_request(u, req)

    affected = []
    try:
        affected = json.loads(req['affected_courses'] or '[]')
    except (ValueError, TypeError):
        affected = []
    # 管理員的改派表單需要導師清單（v1.4.0）
    advisors = (q("SELECT id, name FROM users WHERE role='advisor' AND active=1 "
                  'ORDER BY name') if u['role'] == 'admin' else [])
    return render_template('request_detail.html', req=req, approvals=approvals,
                           logs=logs, can_approve=can_approve, affected=affected,
                           advisors=advisors)


# --------------------------------------------------------------------------
# 審批動作
# --------------------------------------------------------------------------
def _apply_decision(u, req, action, comment, now):
    """把一張假單的審批結果寫入資料庫（v1.6.0 從 decide() 抽出）。

    呼叫端必須先完成前置校驗（status='pending'、can_approve_request、
    action 白名單、comment 長度、駁回必填理由），並負責把本函式包在
    atomic() 內、負責 flash 與 redirect。回傳 (ok, message)：
    ok=False 代表並發下該層審批列已被他人處理（影響列數 0）。

    抽出共用是讓「單張審批」與「批次審批」走同一份寫入邏輯，避免
    核准轉下一級、通知對象、稽核動作文案在兩邊各自漂移。
    """
    rid = req['id']
    level = req['current_level']
    # v1.1.0：action='pending' 條件守衛——兩位審批人同時點擊（或雙擊）
    # 只有第一位能寫入；第二位影響列數 0，不得覆寫第一位結果。
    changed = execute_guard(
        'UPDATE approvals SET action=?, approver_id=?, comment=?, acted_at=? '
        "WHERE request_id=? AND level=? AND action='pending'",
        (action, u['id'], comment, now, rid, level))
    if not changed:
        return False, '該假單剛被其他人處理，請重新整理後再確認。'

    if action == 'reject':
        execute("UPDATE leave_requests SET status='rejected', updated_at=? WHERE id=?", (now, rid))
        log_action(rid, u['id'], 'reject', comment)
        notify(req['student_id'], rid,
               f"你的{req['type_name']}假單被{'導師' if level == 1 else '院系'}駁回：{comment}")
        return True, '已駁回該假單。'

    # 核准
    if level < req['required_level']:
        execute('UPDATE leave_requests SET current_level=?, updated_at=? WHERE id=?',
                (level + 1, now, rid))
        log_action(rid, u['id'], 'approve', f'第{level}級核准，轉院系複核')
        # v1.4.0：院系審批人＋其代理人一起通知
        notify_approvers(2, req['department'], None, rid,
                         f"{req['student_name']} 的 {req['days']} 天{req['type_name']}待院系複核。")
        return True, '已核准，已轉交院系複核。'

    execute("UPDATE leave_requests SET status='approved', updated_at=? WHERE id=?", (now, rid))
    log_action(rid, u['id'], 'approve', f'第{level}級核准，假單生效')
    notify(req['student_id'], rid, f"你的{req['type_name']}假單已核准（{req['days']} 天）。")
    return True, '已核准該假單。'


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

    # 權限校驗（v1.4.0：含代理人身分，統一由 can_approve_request 判斷）
    if not can_approve_request(u, req):
        flash('你沒有權限審批這一單。', 'error')
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
    # v1.1.0：approvals、leave_requests、audit_logs、notifications 四張表
    # 要麼全部生效要麼全部不發生；v1.2.0 改 atomic()，郵件在交易提交後
    # 才寄，不讓 SMTP 連線期間持有 SQLite 寫入鎖。
    # v1.6.0：寫入邏輯抽到 _apply_decision()，單張／批次共用同一份規則。
    with atomic():
        ok, message = _apply_decision(u, req, action, comment, now)
    flash(message, 'success' if ok else 'error')
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
    # v1.1.0：status='pending' 直接寫進 WHERE——「讀取時仍待審」與
    # 「寫入時仍待審」之間若有併發（導師剛好核准），影響列數為 0，
    # 明確告知使用者，而不是覆寫掉剛發生的審批結果。
    # 撤回＋紀錄＋通知同樣在一個交易內完成（atomic() 見 v1.2.0：
    # 郵件在提交後才寄出）。
    with atomic():
        changed = execute_guard(
            "UPDATE leave_requests SET status='withdrawn', updated_at=? "
            "WHERE id=? AND status='pending'", (now, rid))
        if not changed:
            flash('該假單剛被處理，無法撤回。', 'error')
            return redirect(url_for('detail', rid=rid))
        log_action(rid, u['id'], 'withdraw', '學生撤回')
        # v1.0.0：撤回後通知導師——否則導師點開假單才發現已被撤回，
        # 待辦清單裡的數字也與實際對不上。
        # v1.4.0：改走 notify_approvers，代理人同樣收到（他的待辦也會少一筆）。
        stu = q('SELECT advisor_id FROM users WHERE id=?', (u['id'],), one=True)
        if stu and stu['advisor_id']:
            notify_approvers(1, u['department'], stu['advisor_id'], rid,
                             f"{u['name']} 撤回了假單 #{rid}"
                             f"（{req['start_date']} ~ {req['end_date']}）。")
        # v1.1.0：撤回時若已審到院系級，院系審批人也該收到通知——
        # 他們的待辦數字同樣不能對不上。
        if req['current_level'] == 2:
            notify_approvers(2, u['department'], None, rid,
                             f"{u['name']} 撤回了待院系複核的假單 #{rid}"
                             f"（{req['start_date']} ~ {req['end_date']}）。")
    flash('假單已撤回。', 'success')
    return redirect(url_for('my_requests'))


# --------------------------------------------------------------------------
# 待我審批 / 全部假單
# --------------------------------------------------------------------------
@app.route('/approvals')
@login_required
@role_required('advisor', 'faculty', 'admin')
def approvals():
    u = current_user()
    scope = request.args.get('scope', 'todo')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total_pages = 1
    total = 0

    if scope == 'all':
        # 範圍隔離：導師只看自己的學生、院系只看本院系、管理員看全部。
        # v0.9.0 複審發現此分支漏了 role_scope——/stats 與 /export 在 v0.7.0
        # 就修了，唯獨「全部假單」漏網，任何導師都能翻遍全系統的請假事由。
        conds, sargs = role_scope(u)
        # v1.2.0：狀態篩選。全部假單預設混雜四種狀態，審批人想追「已核准」
        # 或「已駁回」只能一頁頁翻；白名單外的值一律視為不篩選。
        status = request.args.get('status', '')
        if status not in LEAVE_STATUSES:
            status = ''
        if status:
            conds.append('r.status=?')
            sargs.append(status)
        where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
        total = q('SELECT COUNT(*) c FROM leave_requests r '
                  'JOIN users s ON s.id=r.student_id ' + where, sargs, one=True)['c']
        total_pages = max((total + per_page - 1) // per_page, 1)
        page = min(max(page, 1), total_pages)
        rows = q('SELECT r.*, t.name type_name, s.name student_name, s.class_name, s.department '
                 'FROM leave_requests r '
                 'JOIN leave_types t ON t.id=r.leave_type_id '
                 'JOIN users s ON s.id=r.student_id '
                 f'{where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?',
                 sargs + [per_page, (page - 1) * per_page])
    else:
        # v1.1.0：待辦清單也分頁。此前 todo 分支一次取出全部待審假單——
        # scope=all 早有分頁，唯獨預設的 todo 視圖沒有，審批人學生一多，
        # 頁面就無限變長。total 交給模板顯示「共 N 筆」。
        status = ''
        total = count_todo(u)
        total_pages = max((total + per_page - 1) // per_page, 1)
        page = min(max(page, 1), total_pages)
        rows = todo_requests(u, limit=per_page, offset=(page - 1) * per_page)
    return render_template('approvals.html', requests=rows, scope=scope,
                           page=page, total_pages=total_pages, total=total,
                           filter_status=status)


# --------------------------------------------------------------------------
# 批次審批（GAP-ANALYSIS B6，v1.6.0）
# --------------------------------------------------------------------------
@app.route('/approvals/bulk', methods=['POST'])
@login_required
@role_required('advisor', 'faculty', 'admin')
def bulk_decide():
    """一次核准／駁回多張待審假單。

    與單張 decide() 共用 _apply_decision()，核准轉下一級、通知對象、
    稽核動作文案與並發守衛因此完全一致。關鍵設計：

    - 逐張重新讀取並校驗 status='pending' 與 can_approve_request()，
      **不信任前端勾選**——批次 id 是可竄改的表單欄位，摻進別人的單
      或已結案的單只能被略過，不能造成越權審批。
    - 整批放進同一個 atomic()：要嘛一起提交，要嘛一起回滾；郵件在
      交易提交後一次寄出（v1.2.0 語義）。
    - 部分成功是允許的：單張被並發處理（execute_guard 影響 0 列）只
      計入 skipped，不讓整批回滾，也不覆寫他人結果。
    """
    u = current_user()
    scope = request.args.get('scope', 'todo')
    if scope not in ('todo', 'all'):
        scope = 'todo'
    back = url_for('approvals', scope=scope)

    raw_ids = request.form.getlist('ids')
    ids = []
    for raw in raw_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid > 0 and rid not in ids:
            ids.append(rid)
    if not ids:
        flash('請先勾選要處理的假單。', 'error')
        return redirect(back)
    if len(ids) > MAX_BULK_DECIDE:
        flash(f'單次最多處理 {MAX_BULK_DECIDE} 張，請分批操作。', 'error')
        return redirect(back)

    action = request.form.get('action')
    comment = request.form.get('comment', '').strip()
    if action not in ('approve', 'reject'):
        flash('無效的操作。', 'error')
        return redirect(back)
    if len(comment) > MAX_COMMENT_LEN:
        flash(f'審批意見過長（上限 {MAX_COMMENT_LEN} 字）。', 'error')
        return redirect(back)
    if action == 'reject' and not comment:
        flash('駁回必須填寫理由。', 'error')
        return redirect(back)

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    done = 0
    skipped = 0
    with atomic():
        for rid in ids:
            req = q('SELECT r.*, t.name type_name, s.name student_name, s.department, '
                    's.advisor_id FROM leave_requests r '
                    'JOIN leave_types t ON t.id=r.leave_type_id '
                    'JOIN users s ON s.id=r.student_id WHERE r.id=?', (rid,), one=True)
            # 逐張重新校驗：不信任前端勾選，越權／已結案一律略過。
            if not req or req['status'] != 'pending' or not can_approve_request(u, req):
                skipped += 1
                continue
            ok, _ = _apply_decision(u, req, action, comment, now)
            if ok:
                done += 1
            else:
                skipped += 1

    if done == 0:
        flash(f'沒有可處理的假單（略過 {skipped} 張：非待審、無權限或已被他人處理）。', 'error')
    else:
        verb = '核准' if action == 'approve' else '駁回'
        flash(f'已{verb} {done} 張假單' + (f'，略過 {skipped} 張。' if skipped else '。'), 'success')
    return redirect(back)


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


@app.route('/notifications/<int:nid>/read', methods=['POST'])
@login_required
def notification_read(nid):
    """單則標為已讀（v1.1.0）：此前只有「全部標為已讀」，
    想保留其餘未讀狀態、逐則處理時沒有辦法。"""
    u = current_user()
    changed = execute_guard('UPDATE notifications SET is_read=1 '
                            'WHERE id=? AND user_id=? AND is_read=0', (nid, u['id']))
    if not changed:
        # 不區分「不存在」與「不屬於你」：避免以枚舉通知 id 探測他人資料
        flash('找不到該通知，或它已被標為已讀。', 'error')
    return redirect(url_for('notifications'))


# --------------------------------------------------------------------------
# 學生請假統計（導師/院系/管理員）
# --------------------------------------------------------------------------
@app.route('/stats')
@login_required
@role_required('advisor', 'faculty', 'admin')
def stats():
    u = current_user()
    # v1.9.0：D3 個資治理——統計頁跨範圍讀取學生姓名／學號／院系等 PII，
    # 每次檢視都留稽核軌跡，誰在何時看過全範圍統計可追溯到（GAP-ANALYSIS D3）。
    log_action(None, u['id'], 'view_pii', 'stats 統計頁（跨範圍 PII）')
    conds, args = role_scope(u)
    # v1.4.0：按學期切。預設看目前學期，?term=<id> 可切換；
    # ?term=0 表示不按學期（全部歷史）。
    terms = q('SELECT * FROM terms ORDER BY start_date DESC')
    raw_term = request.args.get('term', '')
    term = None
    if raw_term == '0':
        pass
    elif raw_term.isdigit():
        term = q('SELECT * FROM terms WHERE id=?', (int(raw_term),), one=True)
    else:
        term = current_term()
    if term:
        conds.append('date(r.start_date) BETWEEN date(?) AND date(?)')
        args += [term['start_date'], term['end_date']]
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
    if term:
        type_conds.append('date(r.start_date) BETWEEN date(?) AND date(?)')
        type_args += [term['start_date'], term['end_date']]
    by_type = q("SELECT t.name, COUNT(r.id) cnt, COALESCE(SUM(r.days),0) days "
                'FROM leave_requests r JOIN leave_types t ON t.id=r.leave_type_id '
                'JOIN users s ON s.id=r.student_id '
                f"WHERE {' AND '.join(type_conds)} GROUP BY t.id ORDER BY days DESC",
                type_args)
    return render_template('stats.html', rows=rows, by_type=by_type,
                           terms=terms, term=term, no_term=(raw_term == '0'))


@app.route('/export')
@login_required
@role_required('advisor', 'faculty', 'admin')
def export_csv():
    u = current_user()
    # v1.9.0：D3——匯出含事由等敏感欄位的全範圍 CSV，留稽核軌跡（GAP-ANALYSIS D3）。
    log_action(None, u['id'], 'export', 'leave_requests CSV 匯出（含事由等 PII）')
    conds, args = role_scope(u)
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    sql = ('SELECT r.id, s.username, s.name, s.class_name, s.department, t.name type_name, '
           'r.start_date, r.end_date, r.days, r.reason, r.status, r.current_level, r.created_at '
           'FROM leave_requests r JOIN users s ON s.id=r.student_id '
           f'JOIN leave_types t ON t.id=r.leave_type_id {where} '
           'ORDER BY r.created_at DESC')

    def generate():
        # v1.2.0：改串流。舊版 q() 一次 fetchall 把整個範圍的假單讀進
        # 記憶體——導師帶幾十屆學生時，匯出能吃到數百 MB。現在用同一條
        # 連線的 cursor 逐批 fetchmany(200) 邊讀邊寫，記憶體用量與
        # 資料量脫鉤。stream_with_context 讓 app context 在串流期間
        # 保持存活，get_db() 才拿得到連線。
        buf = io.StringIO()
        buf.write('\ufeff')
        w = csv.writer(buf)
        w.writerow(['假單號', '學號', '姓名', '班級', '院系', '假別', '開始', '結束',
                    '天數', '事由', '狀態', '目前層級', '提交時間'])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        cur = get_db().execute(sql, args)
        try:
            while True:
                batch = cur.fetchmany(200)
                if not batch:
                    break
                for r in batch:
                    # csv_safe：事由等自由文字若是 = + - @ 開頭，在 Excel 會被當公式執行
                    w.writerow([csv_safe(v) if isinstance(v, str) else v
                                for v in (r['id'], r['username'], r['name'], r['class_name'],
                                          r['department'], r['type_name'], r['start_date'],
                                          r['end_date'], r['days'], r['reason'],
                                          STATUS_LABEL.get(r['status'], r['status']),
                                          r['current_level'], r['created_at'])])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
        finally:
            cur.close()

    return Response(stream_with_context(generate()), mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename=leave_{date.today():%Y%m%d}.csv'})


# --------------------------------------------------------------------------
# 管理後台
# --------------------------------------------------------------------------
def _render_admin_users(role='', page=1, onetime=None):
    """帳號管理頁的共用渲染（GET 與 POST 成功後的一次性呈現都用它）。

    v1.2.0：加分頁。此前一次載入全部帳號——與 v1.1.0 修待辦清單時
    同類的問題：帳號一多頁面就無限變長。角色篩選與分頁可組合。
    """
    page = max(int(page or 1), 1)
    per_page = 20
    where = 'WHERE u.role=?' if role else ''
    args = [role] if role else []
    total = q('SELECT COUNT(*) c FROM users u ' + where, args, one=True)['c']
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    sql = ('SELECT u.*, a.name advisor_name FROM users u '
           'LEFT JOIN users a ON a.id=u.advisor_id '
           + where + ' ORDER BY u.role, u.username LIMIT ? OFFSET ?')
    advisors = q("SELECT id, name FROM users WHERE role='advisor' AND active=1 ORDER BY name")
    return render_template('admin_users.html', users=q(sql, args + [per_page, (page - 1) * per_page]),
                           advisors=advisors, filter_role=role,
                           page=page, total_pages=total_pages, total=total,
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
    me = current_user()
    # v1.9.0：D3——帳號管理頁列出全體帳號的 email 等 PII，留稽核軌跡（GAP-ANALYSIS D3）。
    log_action(None, me['id'], 'view_pii', 'admin/users 帳號清單（PII）')
    return _render_admin_users(request.args.get('role', ''),
                               page=request.args.get('page', 1, type=int))


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
    # v1.1.0：學生與院系帳號必須填寫院系。待辦清單、核准通知、範圍隔離
    # 全都按 department 匹配——空院系的學生遞交需院系複核的假單後，
    # 沒有任何院系審批人看得見它，整張單卡在第二級沒人處理。
    if role in ('student', 'faculty') and not department:
        flash('學生與院系帳號必須填寫所屬院系（審批範圍依院系匹配）。', 'error')
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
    target = q('SELECT id, username, password_hash FROM users WHERE id=?', (uid,), one=True)
    if not target:
        flash('找不到該帳號。', 'error')
        return redirect(url_for('admin_users'))
    new = request.form.get('password', '').strip() or temp_password()
    err = validate_password(new, target['username'])
    if err:
        flash(err, 'error')
        return redirect(url_for('admin_users'))
    # v1.0.0：重設成與目前相同的密碼是無效操作，還給人「已經重設過」的錯覺
    if check_password_hash(target['password_hash'], new):
        flash('新密碼不可與目前密碼相同。', 'error')
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
    target = q('SELECT id, role FROM users WHERE id=?', (uid,), one=True)
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
    department = request.form.get('department', '').strip()
    # 同新增帳號：學生與院系不可清空院系，否則審批範圍再也匹配不到他們
    if target['role'] in ('student', 'faculty') and not department:
        flash('學生與院系帳號必須填寫所屬院系（審批範圍依院系匹配）。', 'error')
        return redirect(url_for('admin_users'))
    execute('UPDATE users SET name=?, department=?, class_name=?, email=?, advisor_id=? '
            'WHERE id=?',
            (name, department,
             request.form.get('class_name', '').strip(),
             email,
             advisor_id, uid))
    log_action(None, current_user()['id'], 'edit_user', f'修改帳號 #{uid} 資料')
    flash('帳號資料已更新。', 'success')
    return redirect(url_for('admin_users'))


# --------------------------------------------------------------------------
# 改派與委託（B4 · v1.4.0）
# --------------------------------------------------------------------------
@app.route('/admin/request/<int:rid>/reassign', methods=['POST'])
@login_required
@role_required('admin')
def admin_reassign(rid):
    """管理員手動改派：把該生的導師歸屬改成另一位導師。

    為什麼改「學生的導師」而不是單張假單的審批人：待辦清單、通知、
    範圍隔離全部按 `users.advisor_id` 匹配。只改單張假單的審批人，
    會出現「這單看得到、下單看不到」的分裂狀態。導師異動本來就是
    學生層級的事實，改在這裡最一致。
    """
    me = current_user()
    req = q('SELECT r.id, r.status, r.current_level, r.student_id, '
            's.name student_name, s.advisor_id '
            'FROM leave_requests r JOIN users s ON s.id=r.student_id '
            'WHERE r.id=?', (rid,), one=True)
    if not req:
        flash('找不到該假單。', 'error')
        return redirect(url_for('admin_users'))
    advisor_id, adv_err = _parse_advisor_id(request.form.get('advisor_id'))
    if adv_err:
        flash(adv_err, 'error')
        return redirect(url_for('detail', rid=rid))
    if advisor_id == req['advisor_id']:
        flash('該生已由這位導師指導，無需改派。', 'error')
        return redirect(url_for('detail', rid=rid))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with atomic():
        execute('UPDATE users SET advisor_id=? WHERE id=?', (advisor_id, req['student_id']))
        log_action(rid, me['id'], 'reassign',
                   f"學生 {req['student_name']} 的導師由 #{req['advisor_id'] or '無'} "
                   f"改派為 #{advisor_id or '無'}")
        if advisor_id and req['status'] == 'pending' and req['current_level'] == 1:
            notify_approvers(1, '', advisor_id, rid,
                             f"{req['student_name']} 的假單 #{rid} 已改派給你審批。")
        notify(req['student_id'], rid,
               f"你的導師歸屬已由教務處調整，假單 #{rid} 之後由新導師處理。")
    flash('已改派該生的導師歸屬。', 'success')
    return redirect(url_for('detail', rid=rid))


@app.route('/admin/delegations')
@login_required
@role_required('admin')
def admin_delegations():
    """委託審批管理：誰在什麼期間把哪一級交給誰。"""
    today = date.today().isoformat()
    rows = q('SELECT d.*, a.name delegator_name, a.role delegator_role, '
             'a.department delegator_dept, b.name delegate_name, b.role delegate_role, '
             "CASE WHEN date(?) BETWEEN date(d.start_date) AND date(d.end_date) "
             "     THEN 1 ELSE 0 END active "
             'FROM delegations d '
             'JOIN users a ON a.id=d.delegator_id '
             'JOIN users b ON b.id=d.delegate_id '
             'ORDER BY d.start_date DESC, d.id DESC', (today,))
    staff = q("SELECT id, name, role, department FROM users "
              "WHERE role IN ('advisor','faculty') AND active=1 ORDER BY role, name")
    return render_template('admin_delegations.html', rows=rows, staff=staff,
                           today=today)


@app.route('/admin/delegations/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_delegation():
    try:
        level = int(request.form.get('level', '1'))
        start = parse_date(request.form.get('start_date', ''))
        end = parse_date(request.form.get('end_date', ''))
    except (TypeError, ValueError):
        flash('委託期間的日期格式不合法。', 'error')
        return redirect(url_for('admin_delegations'))
    if level not in (1, 2):
        flash('委託層級只能是 1（導師）或 2（院系）。', 'error')
        return redirect(url_for('admin_delegations'))
    if end < start:
        flash('委託結束日不可早於開始日。', 'error')
        return redirect(url_for('admin_delegations'))
    try:
        delegator_id = int(request.form.get('delegator_id', ''))
        delegate_id = int(request.form.get('delegate_id', ''))
    except (TypeError, ValueError):
        flash('請選擇委託人與代理人。', 'error')
        return redirect(url_for('admin_delegations'))
    if delegator_id == delegate_id:
        flash('委託人與代理人不可為同一人。', 'error')
        return redirect(url_for('admin_delegations'))
    want_role = 'advisor' if level == 1 else 'faculty'
    a = q('SELECT id, name, role, active FROM users WHERE id=?', (delegator_id,), one=True)
    b = q('SELECT id, name, role, active FROM users WHERE id=?', (delegate_id,), one=True)
    if not a or not b:
        flash('委託人或代理人不存在。', 'error')
        return redirect(url_for('admin_delegations'))
    if a['role'] != want_role or b['role'] != want_role:
        flash(f"第 {level} 級的委託人與代理人都必須是"
              f"{'導師' if level == 1 else '院系教師'}角色。", 'error')
        return redirect(url_for('admin_delegations'))
    if not a['active'] or not b['active']:
        flash('停用的帳號不可參與委託。', 'error')
        return redirect(url_for('admin_delegations'))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with atomic():
        try:
            execute('INSERT INTO delegations (delegator_id, delegate_id, level, '
                    'start_date, end_date, note) VALUES (?,?,?,?,?,?)',
                    (delegator_id, delegate_id, level, start.isoformat(), end.isoformat(),
                     request.form.get('note', '').strip()[:200]))
        except Exception:  # noqa: BLE001 — 重複的委託撞 UNIQUE，提示而非 500
            flash('相同的委託（人、層級、期間）已存在。', 'error')
            return redirect(url_for('admin_delegations'))
        log_action(None, current_user()['id'], 'add_delegation',
                   f"{a['name']} → {b['name']}（第 {level} 級，"
                   f"{start.isoformat()} ~ {end.isoformat()}）")
        notify(delegate_id, None,
               f"{a['name']} 已將第 {level} 級審批權委託給你（{start.isoformat()} ~ "
               f"{end.isoformat()}），期間你會在待辦看到其範圍內的假單。")
    flash(f"已新增委託：{a['name']} → {b['name']}。", 'success')
    return redirect(url_for('admin_delegations'))


@app.route('/admin/delegations/<int:did>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_delegation(did):
    d = q('SELECT d.*, a.name delegator_name, b.name delegate_name '
          'FROM delegations d JOIN users a ON a.id=d.delegator_id '
          'JOIN users b ON b.id=d.delegate_id WHERE d.id=?', (did,), one=True)
    if not d:
        flash('找不到該委託。', 'error')
        return redirect(url_for('admin_delegations'))
    with atomic():
        execute('DELETE FROM delegations WHERE id=?', (did,))
        log_action(None, current_user()['id'], 'del_delegation',
                   f"刪除委託 {d['delegator_name']} → {d['delegate_name']}"
                   f"（第 {d['level']} 級）")
        notify(d['delegate_id'], None,
               f"{d['delegator_name']} 的第 {d['level']} 級委託已被取消。")
    flash('已刪除該委託。', 'success')
    return redirect(url_for('admin_delegations'))


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
    if not valid_type_code(code):
        flash('代號格式不合法（1–20 字元，僅字母、數字與 - _）。', 'error')
        return redirect(url_for('admin_types'))
    if len(name) > MAX_TYPE_NAME_LEN:
        flash(f'假別名稱過長（上限 {MAX_TYPE_NAME_LEN} 字）。', 'error')
        return redirect(url_for('admin_types'))
    # 預先查重：leave_types.code 有 UNIQUE 約束，直接 INSERT 重複代號
    # 會撞 IntegrityError 變成 500（v0.9.1 修）
    if q('SELECT id FROM leave_types WHERE code=?', (code,), one=True):
        flash(f'代號 {code} 已存在。', 'error')
        return redirect(url_for('admin_types'))
    execute('INSERT INTO leave_types (code, name, requires_attachment, needs_faculty) '
            'VALUES (?,?,?,?)',
            (code, name, 1 if request.form.get('requires_attachment') else 0,
             1 if request.form.get('needs_faculty') else 0))
    log_action(None, current_user()['id'], 'add_type', f'新增假別 {code}')
    flash(f'已新增假別「{name}」。', 'success')
    return redirect(url_for('admin_types'))


@app.route('/admin/types/<int:tid>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_type(tid):
    """停用／啟用假別。停用後不出現在學生提交表單；既有假單不受影響
    （查詢依 id JOIN，與 active 旗標無關）。"""
    target = q('SELECT id, code, name, active FROM leave_types WHERE id=?', (tid,), one=True)
    if not target:
        flash('找不到該假別。', 'error')
        return redirect(url_for('admin_types'))
    new_active = 0 if target['active'] else 1
    execute('UPDATE leave_types SET active=? WHERE id=?', (new_active, tid))
    log_action(None, current_user()['id'], 'toggle_type',
               f'{"停用" if target["active"] else "啟用"}假別 {target["code"]}')
    flash(f'已{"停用" if target["active"] else "啟用"}假別「{target["name"]}」。', 'success')
    return redirect(url_for('admin_types'))


# --------------------------------------------------------------------------
# 學期 / 額度 / 校曆（C2 + C3 · v1.4.0）
# --------------------------------------------------------------------------
@app.route('/admin/terms')
@login_required
@role_required('admin')
def admin_terms():
    terms = q('SELECT * FROM terms ORDER BY start_date DESC')
    quotas = q('SELECT qt.*, t.name type_name, t.code type_code, tm.label term_label '
               'FROM leave_quotas qt JOIN leave_types t ON t.id=qt.leave_type_id '
               'JOIN terms tm ON tm.id=qt.term_id '
               'ORDER BY tm.start_date DESC, t.id')
    holidays = q('SELECT * FROM holidays ORDER BY day')
    # v1.5.0：補課日 + 可一鍵帶入的內建校曆
    makeups = [dict(m, cover_label=WEEKDAY_LABELS[m['cover_weekday']])
               for m in q('SELECT * FROM makeup_days ORDER BY day')]
    presets = [{'key': k, 'label': v[1]} for k, v in PRESETS.items()]
    types = q('SELECT id, code, name FROM leave_types WHERE active=1 ORDER BY id')
    return render_template('admin_terms.html', terms=terms, quotas=quotas,
                           holidays=holidays, types=types, makeups=makeups,
                           presets=presets, cur=current_term())


@app.route('/admin/terms/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_term():
    label = request.form.get('label', '').strip()
    try:
        start = parse_date(request.form.get('start_date', ''))
        end = parse_date(request.form.get('end_date', ''))
    except (TypeError, ValueError):
        flash('學期日期格式不合法。', 'error')
        return redirect(url_for('admin_terms'))
    if not label:
        flash('學期標籤必填（例：2026-27 秋季）。', 'error')
        return redirect(url_for('admin_terms'))
    if len(label) > 40:
        flash('學期標籤過長（上限 40 字）。', 'error')
        return redirect(url_for('admin_terms'))
    if end < start:
        flash('學期結束日不可早於開始日。', 'error')
        return redirect(url_for('admin_terms'))
    if q('SELECT id FROM terms WHERE label=?', (label,), one=True):
        flash(f'學期「{label}」已存在。', 'error')
        return redirect(url_for('admin_terms'))
    make_current = 1 if request.form.get('is_current') else 0
    with atomic():
        if make_current:
            execute('UPDATE terms SET is_current=0')
        execute('INSERT INTO terms (label, start_date, end_date, is_current) '
                'VALUES (?,?,?,?)', (label, start.isoformat(), end.isoformat(), make_current))
        log_action(None, current_user()['id'], 'add_term',
                   f'{label}（{start.isoformat()} ~ {end.isoformat()}）')
    flash(f'已新增學期「{label}」。', 'success')
    return redirect(url_for('admin_terms'))


@app.route('/admin/terms/<int:tid>/current', methods=['POST'])
@login_required
@role_required('admin')
def admin_set_current_term(tid):
    t = q('SELECT * FROM terms WHERE id=?', (tid,), one=True)
    if not t:
        flash('找不到該學期。', 'error')
        return redirect(url_for('admin_terms'))
    with atomic():
        execute('UPDATE terms SET is_current=0')
        execute('UPDATE terms SET is_current=1 WHERE id=?', (tid,))
        log_action(None, current_user()['id'], 'set_current_term', t['label'])
    flash(f"已將「{t['label']}」設為目前學期。", 'success')
    return redirect(url_for('admin_terms'))


@app.route('/admin/quotas/set', methods=['POST'])
@login_required
@role_required('admin')
def admin_set_quota():
    try:
        term_id = int(request.form.get('term_id', ''))
        type_id = int(request.form.get('leave_type_id', ''))
    except (TypeError, ValueError):
        flash('請選擇學期與假別。', 'error')
        return redirect(url_for('admin_terms'))
    raw = request.form.get('max_days', '').strip()
    if not raw:
        execute("DELETE FROM leave_quotas WHERE term_id=? AND leave_type_id=? AND scope='all'",
                (term_id, type_id))
        flash('已取消該假別的額度限制。', 'success')
        return redirect(url_for('admin_terms'))
    try:
        max_days = float(raw)
    except ValueError:
        flash('額度必須是數字。', 'error')
        return redirect(url_for('admin_terms'))
    if max_days < 0 or max_days > 365:
        flash('額度需介於 0 與 365 天之間。', 'error')
        return redirect(url_for('admin_terms'))
    if not q('SELECT 1 FROM terms WHERE id=?', (term_id,), one=True) or \
            not q('SELECT 1 FROM leave_types WHERE id=?', (type_id,), one=True):
        flash('學期或假別不存在。', 'error')
        return redirect(url_for('admin_terms'))
    execute('INSERT INTO leave_quotas (term_id, leave_type_id, max_days, scope, scope_value) '
            "VALUES (?,?,?,'all','') "
            'ON CONFLICT(term_id, leave_type_id, scope, scope_value) '
            'DO UPDATE SET max_days=excluded.max_days',
            (term_id, type_id, max_days))
    log_action(None, current_user()['id'], 'set_quota',
               f'學期 #{term_id} 假別 #{type_id} 額度 {max_days:g} 天')
    flash('已更新額度。', 'success')
    return redirect(url_for('admin_terms'))


@app.route('/admin/holidays/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_holiday():
    raw = request.form.get('day', '').strip()
    try:
        day = parse_date(raw)
    except (TypeError, ValueError):
        flash('日期格式不合法（YYYY-MM-DD）。', 'error')
        return redirect(url_for('admin_terms'))
    label = request.form.get('label', '').strip()[:60]
    execute('INSERT INTO holidays (day, label) VALUES (?,?) '
            'ON CONFLICT(day) DO UPDATE SET label=excluded.label',
            (day.isoformat(), label))
    log_action(None, current_user()['id'], 'add_holiday', f'{day.isoformat()} {label}')
    flash(f'已加入校曆假日 {day.isoformat()}。', 'success')
    return redirect(url_for('admin_terms'))


@app.route('/admin/holidays/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_holidays():
    """批次匯入校曆：每行 `YYYY-MM-DD` 或 `YYYY-MM-DD,標籤`。

    手動一天天加整學期的假期不現實——校曆通常是教務處給的一份清單，
    貼進來就好。
    """
    raw = request.form.get('lines', '')
    added = skipped = 0
    bad = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        parts = [p.strip() for p in ln.replace('\t', ',').split(',', 1)]
        try:
            day = parse_date(parts[0])
        except (TypeError, ValueError):
            bad.append(parts[0])
            continue
        label = parts[1] if len(parts) > 1 else ''
        execute('INSERT INTO holidays (day, label) VALUES (?,?) '
                'ON CONFLICT(day) DO UPDATE SET label=excluded.label',
                (day.isoformat(), label[:60]))
        added += 1
    if bad:
        skipped = len(bad)
    log_action(None, current_user()['id'], 'add_holiday',
               f'批次匯入 {added} 天（略過 {skipped} 行格式錯誤）')
    msg = f'已匯入 {added} 個校曆假日。'
    if skipped:
        msg += f'另有 {skipped} 行格式不合法已略過。'
    flash(msg, 'success' if added else 'error')
    return redirect(url_for('admin_terms'))


@app.route('/admin/holidays/<int:hid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_holiday(hid):
    h = q('SELECT * FROM holidays WHERE id=?', (hid,), one=True)
    if not h:
        flash('找不到該假日。', 'error')
        return redirect(url_for('admin_terms'))
    execute('DELETE FROM holidays WHERE id=?', (hid,))
    log_action(None, current_user()['id'], 'del_holiday', h['day'])
    flash(f"已移除 {h['day']}。", 'success')
    return redirect(url_for('admin_terms'))


# --------------------------------------------------------------------------
# 校曆檔匯入（v1.5.0）
# --------------------------------------------------------------------------
@app.route('/admin/calendar/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_calendar():
    """一次匯入「學期 + 假日 + 補課日」。

    單筆加假日對一整份校曆不現實：光國慶節就要連按七天。校曆檔支援
    日期區間，也把學期與補課日一起收進來——匯入一次，整學年的日期
    邊界就都對了。
    """
    preset = request.form.get('preset', '').strip()
    if preset:
        text = load_preset(preset)
        source = PRESETS.get(preset, (preset, preset))[1]
        if text is None:
            flash('找不到指定的內建校曆。', 'error')
            return redirect(url_for('admin_terms'))
    else:
        text = request.form.get('calendar_text', '')
        source = '手動貼上'
    parsed = parse_calendar_text(text)
    if not (parsed['terms'] or parsed['holidays'] or parsed['makeups']):
        flash('沒有解析到任何項目，請檢查檔案格式。', 'error')
        return redirect(url_for('admin_terms'))
    try:
        stats = apply_calendar(parsed)
    except Exception:                      # 匯入寫入失敗不該把整頁炸掉
        flash('匯入時發生錯誤，資料庫未變更。', 'error')
        return redirect(url_for('admin_terms'))
    log_action(None, current_user()['id'], 'import_calendar',
               f"{source}：學期 {stats['terms']}、假日 {stats['holidays']} 天、"
               f"補課 {stats['makeups']} 天")
    msg = (f"已匯入 {source}：{stats['terms']} 個學期、"
           f"{stats['holidays']} 個假日、{stats['makeups']} 個補課日。")
    if parsed['errors']:
        bad = '、'.join(f"第 {e['line']} 行" for e in parsed['errors'][:5])
        msg += f"（{len(parsed['errors'])} 行格式有誤已略過：{bad}）"
        flash(msg, 'error' if not stats['terms'] else 'success')
    else:
        flash(msg, 'success')
    return redirect(url_for('admin_terms'))


@app.route('/admin/makeup/<int:mid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_makeup(mid):
    m = q('SELECT * FROM makeup_days WHERE id=?', (mid,), one=True)
    if not m:
        flash('找不到該補課日。', 'error')
        return redirect(url_for('admin_terms'))
    execute('DELETE FROM makeup_days WHERE id=?', (mid,))
    log_action(None, current_user()['id'], 'del_makeup', m['day'])
    flash(f"已移除補課日 {m['day']}。", 'success')
    return redirect(url_for('admin_terms'))


# --------------------------------------------------------------------------
# 課表管理（v1.7.0 · C1）
# --------------------------------------------------------------------------
@app.route('/admin/timetable')
@login_required
@role_required('admin')
def admin_timetable():
    """逐生課表維護：選學生 → 看週課表 → 單筆增刪，或整份 CSV 匯入。

    課表是「受影響課程」的唯一依據。它一直是全體共用的一份示範資料，
    所以這裡把「多少人的課表還是假的」放在最上面——不解決這個，
    整套課表感知功能都只是裝飾。
    """
    uid = request.args.get('uid', type=int)
    students = q("SELECT id, username, name, class_name, department FROM users "
                 "WHERE role='student' AND active=1 ORDER BY username")
    sel, rows, by_day = None, [], {}
    if uid:
        sel = q('SELECT id, username, name, class_name, department FROM users '
                "WHERE id=? AND role='student'", (uid,), one=True)
        if sel:
            rows = _timetable_by_student(sel['id'])
            for r in rows:
                by_day.setdefault(r['weekday'], []).append(r)
    return render_template('admin_timetable.html', students=students, sel=sel,
                           rows=rows, by_day=by_day,
                           coverage=timetable_coverage(),
                           days=[(i, WEEKDAY_LABELS[i]) for i in range(5)],
                           max_rows=MAX_IMPORT_ROWS)


@app.route('/admin/timetable/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_add_timetable_row():
    """單筆新增一節課（少量修正用；整份課表請走匯入）。"""
    try:
        uid = int(request.form.get('uid', ''))
        wd = int(request.form.get('weekday', ''))
    except (TypeError, ValueError):
        flash('請選擇學生與星期。', 'error')
        return redirect(url_for('admin_timetable'))
    student = q('SELECT id, name FROM users WHERE id=? AND role=?',
                (uid, 'student'), one=True)
    if not student:
        flash('找不到該學生。', 'error')
        return redirect(url_for('admin_timetable'))
    if not 0 <= wd <= 4:
        flash('星期只接受週一到週五。', 'error')
        return redirect(url_for('admin_timetable', uid=uid))
    start_t = request.form.get('start_time', '').strip()
    end_t = request.form.get('end_time', '').strip()
    if not (is_hhmm(start_t) and is_hhmm(end_t)):
        flash('時間格式需為 HH:MM（24 小時制）。', 'error')
        return redirect(url_for('admin_timetable', uid=uid))
    # 統一補零後再比大小：'9:00' < '12:00' 是 False，直接比會把上午課判成下午
    start_t, end_t = pad_time(start_t), pad_time(end_t)
    if end_t <= start_t:
        flash('結束時間需晚於開始時間。', 'error')
        return redirect(url_for('admin_timetable', uid=uid))
    code = request.form.get('course_code', '').strip()
    if not code:
        flash('課程代碼必填。', 'error')
        return redirect(url_for('admin_timetable', uid=uid))
    row = {'weekday': wd, 'start_time': start_t, 'end_time': end_t,
           'course_code': code[:40], 'kind': request.form.get('kind', '').strip()[:20],
           'room': request.form.get('room', '').strip()[:60],
           'note': request.form.get('note', '').strip()[:80]}
    with atomic():
        # 手動新增只動這一節，不動學生其餘課表（先去重是為了舊庫沒有唯一索引時
        # 仍不會長出重複的課）
        execute('DELETE FROM timetable WHERE student_id=? AND weekday=? '
                'AND start_time=? AND end_time=? AND course_code=?',
                (uid, wd, start_t, end_t, row['course_code']))
        execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
                "course_code, kind, room, note, source) VALUES (?,?,?,?,?,?,?,?,'manual')",
                (uid, wd, start_t, end_t, row['course_code'], row['kind'],
                 row['room'], row['note']))
        log_action(None, current_user()['id'], 'add_timetable',
                   f"{student['name']}（#{uid}）{WEEKDAY_LABELS[wd]} "
                   f"{start_t}-{end_t} {row['course_code']}")
    flash(f"已加入 {WEEKDAY_LABELS[wd]} {start_t}-{end_t} {row['course_code']}。",
          'success')
    return redirect(url_for('admin_timetable', uid=uid))


@app.route('/admin/timetable/<int:tid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_timetable_row(tid):
    r = q('SELECT t.*, u.name student_name FROM timetable t '
          'JOIN users u ON u.id=t.student_id WHERE t.id=?', (tid,), one=True)
    if not r:
        flash('找不到該節課。', 'error')
        return redirect(url_for('admin_timetable'))
    with atomic():
        execute('DELETE FROM timetable WHERE id=?', (tid,))
        log_action(None, current_user()['id'], 'del_timetable',
                   f"{r['student_name']}（#{r['student_id']}）"
                   f"{WEEKDAY_LABELS[r['weekday']]} {r['start_time']}-{r['end_time']} "
                   f"{r['course_code']}")
    flash(f"已移除 {r['course_code']}。", 'success')
    return redirect(url_for('admin_timetable', uid=r['student_id']))


@app.route('/admin/timetable/import', methods=['POST'])
@login_required
@role_required('admin')
def admin_import_timetable():
    """CSV 匯入課表：貼上文字或上傳檔案。

    mode=replace（預設）對「檔案裡出現的每一位學生」先清空再寫入——
    教務處匯出的是整份課表，舊資料留著只會造成同一時段兩門課。
    mode=merge 只新增／更新，適合增量補課。
    """
    mode = 'replace' if request.form.get('mode', 'replace') == 'replace' else 'merge'
    text = request.form.get('csv_text', '')
    f = request.files.get('csv_file')
    if f and f.filename:
        try:
            text = f.read().decode('utf-8-sig', errors='replace')
        except OSError:
            flash('讀取上傳檔案失敗。', 'error')
            return redirect(url_for('admin_timetable'))
    parsed = parse_timetable_csv(text)
    if not parsed['rows']:
        flash('沒有解析到任何課，請檢查格式（學號,星期,開始,結束,課程代碼）。',
              'error')
        return redirect(url_for('admin_timetable'))
    try:
        with atomic():
            stats = apply_timetable(parsed, mode=mode)
            log_action(None, current_user()['id'], 'import_timetable',
                       f"{mode} 模式：{stats['students']} 位學生、"
                       f"{stats['rows']} 節課"
                       + (f"、清除舊課 {stats['deleted']} 節" if mode == 'replace' else '')
                       + (f"、學號查無此人 {len(stats['unknown'])} 個"
                          if stats['unknown'] else ''))
    except Exception:                  # 寫入失敗不該把整頁炸掉
        flash('匯入時發生錯誤，資料庫未變更。', 'error')
        return redirect(url_for('admin_timetable'))
    msg = (f"已匯入 {stats['rows']} 節課，共 {stats['students']} 位學生"
           + (f"（清除舊課 {stats['deleted']} 節）" if mode == 'replace' else '（增量合併）')
           + '。')
    if stats['unknown']:
        shown = '、'.join(stats['unknown'][:5])
        msg += f"有 {len(stats['unknown'])} 個學號查無帳號已略過：{shown}"
    if parsed['errors']:
        bad = '、'.join(f"第 {e['line']} 行" for e in parsed['errors'][:5])
        msg += f"（{len(parsed['errors'])} 列格式有誤已略過：{bad}）"
    flash(msg, 'success' if stats['rows'] else 'error')
    return redirect(url_for('admin_timetable'))


# 稽核日誌動作的人間可讀標籤（模板與篩選器共用）
AUDIT_ACTION_LABEL = {
    'submit': '提交假單', 'approve': '核准', 'reject': '駁回', 'withdraw': '撤回',
    'add_user': '新增帳號', 'toggle_user': '停用／啟用帳號',
    'reset_password': '重設密碼', 'edit_user': '編輯帳號', 'change_password': '修改密碼',
    'add_type': '新增假別', 'toggle_type': '停用／啟用假別',
    # v1.4.0
    'reassign': '改派導師', 'add_delegation': '新增委託', 'del_delegation': '刪除委託',
    'add_term': '新增學期', 'set_current_term': '設定目前學期', 'set_quota': '設定額度',
    'add_holiday': '新增校曆假日', 'del_holiday': '刪除校曆假日', 'escalate': '催辦升級',
    # v1.5.0
    'import_calendar': '匯入校曆檔', 'del_makeup': '刪除補課日',
    # v1.7.0
    'add_timetable': '新增課表', 'del_timetable': '刪除課表',
    'import_timetable': '匯入課表',
    # v1.9.0：D3 個資治理——PII 讀取類動作的稽核標籤
    'export': '匯出資料', 'view_pii': '檢視個資',
    # v1.10.0：D1 附件留存期銷毀
    'purge_attachment': '銷毀逾期附件',
}


@app.route('/admin/logs')
@login_required
@role_required('admin')
def admin_logs():
    """後台稽核日誌（v1.0.0）：audit_logs 此前只能在假單詳情頁逐張查看，
    帳號與假別管理的操作更是散在各處——這裡依動作篩選、統一翻頁。"""
    action = request.args.get('action', '')
    page = request.args.get('page', 1, type=int)
    per_page = 30

    where = 'WHERE 1=1'
    args = []
    if action:
        where += ' AND l.action=?'
        args.append(action)
    total = q('SELECT COUNT(*) c FROM audit_logs l ' + where, args, one=True)['c']
    total_pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), total_pages)
    rows = q('SELECT l.*, u.name actor_name, u.username actor_username '
             'FROM audit_logs l LEFT JOIN users u ON u.id=l.actor_id '
             f'{where} ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?',
             tuple(args) + (per_page, (page - 1) * per_page))
    actions = [r['action'] for r in
               q('SELECT DISTINCT action FROM audit_logs ORDER BY action')]
    return render_template('admin_logs.html', logs=rows, page=page,
                           total_pages=total_pages, total=total,
                           cur_action=action, actions=actions,
                           audit_labels=AUDIT_ACTION_LABEL)


@app.route('/admin/cron/escalate', methods=['GET', 'POST'])
def cron_escalate():
    """催辦升級定時任務（B5 · v1.4.0）。

    掃描「提交超過 N 天仍是 pending」的假單：
      1. 提醒當前審批人（含代理人）；
      2. 升級通知其上級——第 1 級通知該院系所有院系教師，第 2 級通知管理員；
      3. 寫一筆 audit_log（action='escalate'）當作催辦憑據。

    鑑權：設定 LEAVE_CRON_TOKEN 時以 token 為準（給外部 cron／容器定時器），
    否則要求管理員 session（可在瀏覽器手動觸發）。
    同一張單 24 小時內只催一次，避免每小時跑一次就把通知欄洗版。
    """
    token = os.environ.get('LEAVE_CRON_TOKEN', '')
    if token:
        sent = request.args.get('token') or request.headers.get('X-Cron-Token', '')
        if not sent or not hmac.compare_digest(str(sent), str(token)):
            return jsonify({'status': 'error', 'message': 'invalid token'}), 403
    else:
        u = current_user()
        if not u or u['role'] != 'admin':
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    try:
        days = int(request.args.get('days', os.environ.get('LEAVE_ESCALATE_DAYS', '3')))
    except (TypeError, ValueError):
        days = 3
    days = max(days, 1)
    now = datetime.now()
    cutoff = (now - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    cooldown = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')

    rows = q("SELECT r.id, r.days, r.created_at, r.current_level, r.student_id, "
             's.name student_name, s.department, s.advisor_id, t.name type_name '
             'FROM leave_requests r JOIN users s ON s.id=r.student_id '
             'JOIN leave_types t ON t.id=r.leave_type_id '
             "WHERE r.status='pending' AND r.created_at <= ? "
             'ORDER BY r.created_at ASC', (cutoff,))
    admins = [a['id'] for a in
              q("SELECT id FROM users WHERE role='admin' AND active=1")]

    reminded = escalated = skipped = 0
    for r in rows:
        # 24 小時冷卻：同一張單不重複催
        if q("SELECT 1 FROM audit_logs WHERE request_id=? AND action='escalate' "
             "AND created_at >= ?", (r['id'], cooldown), one=True):
            skipped += 1
            continue
        try:
            age = max((now - _parse_ts(r['created_at'])).days, 0)
        except (ValueError, TypeError):
            age = days
        level = r['current_level']
        msg = (f"【催辦】假單 #{r['id']}（{r['student_name']} {r['days']} 天"
               f"{r['type_name']}）已待審 {age} 天，請儘速處理。")
        with atomic():
            notify_approvers(level, r['department'], r['advisor_id'], r['id'], msg)
            reminded += 1
            # 升級：第 1 級找院系（導師的上級），第 2 級找管理員
            if level == 1:
                notify_approvers(2, r['department'], None, r['id'],
                                 f"【升級】{r['student_name']} 的假單 #{r['id']} "
                                 f"已待導師審批 {age} 天，請協助跟進。")
            for aid in admins:
                notify(aid, r['id'],
                       f"【升級】假單 #{r['id']}（{r['student_name']}）"
                       f"已待第 {level} 級審批 {age} 天。")
            escalated += 1
            log_action(r['id'], None, 'escalate',
                       f'待審 {age} 天，已提醒第 {level} 級審批人並升級通知上級')
    return jsonify({'status': 'ok', 'threshold_days': days, 'scanned': len(rows),
                    'reminded': reminded, 'escalated': escalated,
                    'skipped_cooldown': skipped})


# --------------------------------------------------------------------------
# D1 · 附件留存期與銷毀（v1.10.0）
# --------------------------------------------------------------------------
def purge_expired_attachments(days=None, dry_run=False):
    """銷毀超過留存期的終態假單附件（D1 · v1.10.0）。

    掃描 leave_requests 中「有附件、且狀態為終態（approved／rejected／
    withdrawn）、且 updated_at 距今天數超過留存期」的紀錄：
      1. 先將檔案搬離活動上傳區（uploads/）到備份區（attachments_purged/），
         實作「先備份再銷毀」——活動儲存不再持有個資，但留可恢復副本；
      2. 清空 attachment 欄，切斷連結；
      3. 寫一筆 audit_log（action='purge_attachment'）當作銷毀憑據。

    dry_run=True 時只回報會處理哪些，不動檔案、不寫庫、不留稽核。

    參數：
      days     留存天數；預設取 LEAVE_ATTACHMENT_RETENTION_DAYS（內建 365）。
      dry_run  僅模擬，不實際執行。

    回傳 dict：scanned / purged / missing_file / dry_run / retention_days / details。

    設計說明：病假證明屬健康個資，PIPL 要求留存最小化——假單一旦進入終態，
    證明檔就沒有繼續躺在活動上傳區的理由；超過留存期即銷毀，降低外洩面。
    """
    if days is None:
        try:
            days = int(os.environ.get('LEAVE_ATTACHMENT_RETENTION_DAYS', '365'))
        except (TypeError, ValueError):
            days = 365
    days = max(days, 1)
    now = datetime.now()
    cutoff = (now - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    backup_dir = os.environ.get('LEAVE_ATTACHMENT_BACKUP_DIR') \
        or os.path.join(BASE_DIR, 'attachments_purged')
    os.makedirs(backup_dir, exist_ok=True)

    rows = q("SELECT r.id, r.student_id, r.status, r.attachment, r.updated_at "
             "FROM leave_requests r "
             "WHERE r.attachment != '' "
             "AND r.status IN ('approved','rejected','withdrawn') "
             "AND r.updated_at <= ? "
             "ORDER BY r.updated_at ASC", (cutoff,))

    purged = missing_file = 0
    details = []
    for r in rows:
        fn = r['attachment']
        src = os.path.join(UPLOAD_DIR, fn)
        if not os.path.exists(src):
            # 檔案已不在上傳區（可能曾手動清除）——仍切斷連結並留稽核。
            if not dry_run:
                with atomic():
                    execute('UPDATE leave_requests SET attachment=? WHERE id=?',
                            ('', r['id']))
                    log_action(r['id'], None, 'purge_attachment',
                               f'附件逾期（{r["status"]}，{fn}）檔案已不存在，'
                               f'僅清空 attachment 欄')
            missing_file += 1
            details.append((r['id'], fn, 'missing'))
            continue
        if dry_run:
            details.append((r['id'], fn, 'would_purge'))
            continue
        dest = os.path.join(backup_dir, fn)
        try:
            shutil.move(src, dest)
        except OSError:
            # 備份區寫入失敗：不放棄整批，個別標記後繼續，並在稽核中註明。
            details.append((r['id'], fn, 'backup_failed'))
            continue
        try:
            age = max((now - _parse_ts(r['updated_at'])).days, 0)
        except (ValueError, TypeError):
            age = days
        with atomic():
            execute('UPDATE leave_requests SET attachment=? WHERE id=?',
                    ('', r['id']))
            log_action(r['id'], None, 'purge_attachment',
                       f'附件逾期銷毀（{r["status"]}，留存 {age} 天，{fn}）'
                       f'→ 備份至 {os.path.basename(backup_dir)}/')
        purged += 1
        details.append((r['id'], fn, 'purged'))
    return {'scanned': len(rows), 'purged': purged, 'missing_file': missing_file,
            'dry_run': dry_run, 'retention_days': days, 'details': details}


@app.route('/admin/cron/purge-attachments', methods=['GET', 'POST'])
def cron_purge_attachments():
    """附件留存期銷毀定時任務（D1 · v1.10.0）。

    掃描超過留存期的終態假單附件，先備份再銷毀並留稽核。
    鑑權：同 cron_escalate——設 LEAVE_CRON_TOKEN 時以 token 為準（外部 cron
    ／容器定時器），否則要求管理員 session 手動觸發。支援 dry_run 預覽。
    """
    token = os.environ.get('LEAVE_CRON_TOKEN', '')
    if token:
        sent = request.args.get('token') or request.headers.get('X-Cron-Token', '')
        if not sent or not hmac.compare_digest(str(sent), str(token)):
            return jsonify({'status': 'error', 'message': 'invalid token'}), 403
    else:
        u = current_user()
        if not u or u['role'] != 'admin':
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    dry = (request.args.get('dry_run', '0') == '1'
           or request.form.get('dry_run', '0') == '1')
    try:
        days = int(request.args.get(
            'days', os.environ.get('LEAVE_ATTACHMENT_RETENTION_DAYS', '365')))
    except (TypeError, ValueError):
        days = 365
    days = max(days, 1)

    result = purge_expired_attachments(days=days, dry_run=dry)
    return jsonify({'status': 'ok', **result})


@app.route('/healthz')
def healthz():
    """健康檢查（無需登入）：供負載均衡 / 容器探測 / CI 使用。"""
    try:
        q('SELECT 1')
        q('SELECT COUNT(*) c FROM users')      # schema 完整性：核心表可查
        return jsonify({'status': 'ok', 'db': 'up', 'version': APP_VERSION})
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
    # v1.4.0：代理人與原審批人同權——否則代理了卻打不開病假證明，
    # 只能盲審。
    allowed = (
        u['role'] == 'admin'
        or u['id'] == req['student_id']
        or (u['role'] == 'advisor' and req['advisor_id'] in approvable_advisor_ids(u))
        or (u['role'] == 'faculty' and req['department'] in approvable_departments(u))
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
    seed_calendar()
    print('資料庫已初始化。')


@app.cli.command('import-calendar')
@click.argument('path', required=False)
def import_calendar_command(path):
    """匯入校曆檔：`flask import-calendar data/hkustgz-2026-27.txt`

    公眾假期每年由政府公告（且常與校曆初版不同），公告後重跑一次即可
    更新學期、假日與補課日——不用進管理介面手改。不給 path 時匯入
    涵蓋今天的內建校曆。
    """
    if path:
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except OSError as exc:
            print(f'讀取失敗：{exc}')
            return
    else:
        name = preset_for_today()
        if not name:
            print('找不到涵蓋今天的內建校曆，請指定檔案路徑。')
            return
        text = load_preset(name)
        print(f'使用內建校曆：{name}')
    parsed = parse_calendar_text(text)
    with app.app_context():
        stats = apply_calendar(parsed)
    print(f"學期 {stats['terms']}、假日 {stats['holidays']} 天、"
          f"補課 {stats['makeups']} 天")
    for e in parsed['errors']:
        print(f"  第 {e['line']} 行略過：{e['msg']}（{e['raw']}）")


@app.cli.command('import-timetable')
@click.argument('path')
@click.option('--merge', is_flag=True,
              help='增量合併（預設：對檔案中的學生先清空再寫入）')
def import_timetable_command(path, merge):
    """匯入課表 CSV：`flask import-timetable data/timetable-sample.csv`

    教務處每學期匯出一次課表，掛進排程就不用人手維護。重跑同一份檔
    不會長出重複的課（同一學生同時段同一門課會被更新）。
    格式：學號,星期,開始時間,結束時間,課程代碼[,類型,教室,備註]
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            text = f.read()
    except OSError as exc:
        print(f'讀取失敗：{exc}')
        return
    parsed = parse_timetable_csv(text)
    if not parsed['rows']:
        print('沒有解析到任何課，請檢查檔案格式。')
        for e in parsed['errors'][:10]:
            print(f"  第 {e['line']} 行：{e['msg']}（{e['raw']}）")
        return
    with app.app_context(), atomic():
        stats = apply_timetable(parsed, mode='merge' if merge else 'replace')
        log_action(None, None, 'import_timetable',
                   f"CLI {path}（{'merge' if merge else 'replace'}）："
                   f"{stats['students']} 位學生、{stats['rows']} 節課")
    print(f"匯入完成：{stats['students']} 位學生、{stats['rows']} 節課"
          + (f"（清除舊課 {stats['deleted']} 節）" if not merge else '（增量合併）'))
    if stats['unknown']:
        print(f"學號查無帳號已略過 {len(stats['unknown'])} 個："
              + '、'.join(stats['unknown'][:10]))
    for e in parsed['errors']:
        print(f"  第 {e['line']} 行略過：{e['msg']}（{e['raw']}）")


@app.cli.command('purge-attachments')
@click.option('--days', default=None, type=int,
              help='留存天數（預設取 LEAVE_ATTACHMENT_RETENTION_DAYS，內建 365）')
@click.option('--dry-run', is_flag=True, help='只預覽會處理哪些，不動檔案不寫庫')
def purge_attachments_command(days, dry_run):
    """銷毀超過留存期的終態假單附件（D1）：flask purge-attachments [--days N] [--dry-run]

    病假證明等附件屬健康個資，依 PIPL 留存最小化，終態假單超過留存期即銷毀。
    先備份（attachments_purged/）再清空 attachment 欄並留稽核軌跡。
    """
    result = purge_expired_attachments(days=days, dry_run=dry_run)
    print(f"掃描 {result['scanned']} 筆、已銷毀 {result['purged']} 筆、"
          f"檔案遺失僅清欄 {result['missing_file']} 筆"
          + ("（dry-run，未實際執行）" if result['dry_run'] else ""))
    for rid, fn, status in result['details']:
        print(f"  #{rid} {fn} → {status}")


def seed_calendar():
    """示範學期與校曆（冪等）。

    刻意與 seed() 分開：seed() 只在空庫寫示範帳號，測試也是走
    init_db() + seed()。學期一旦寫入，/stats 預設就會按學期篩選——
    為了不讓既有測試的資料範圍被學期邊界切掉，學期只在實際啟動
    應用（`python app.py` 或 `flask init-db`）時才建立。

    v1.5.0：data/ 下若有涵蓋今天的內建校曆檔（例如港科大（廣州）
    2026-27），就直接匯入真實校曆，不再用下面的佔位學期。學年往前走
    時只要把新學年的校曆檔放進 data/，不必改程式碼。
    """
    preset = preset_for_today()
    if preset and not q('SELECT 1 FROM terms LIMIT 1'):
        parsed = parse_calendar_text(load_preset(preset) or '')
        if parsed['terms'] and not parsed['errors']:
            stats = apply_calendar(parsed)
            print(f"已匯入內建校曆 {preset}：{stats['terms']} 個學期、"
                  f"{stats['holidays']} 個假日、{stats['makeups']} 個補課日。")
            return
        print(f"內建校曆 {preset} 解析有誤，改用佔位學期。")

    if not q('SELECT 1 FROM terms LIMIT 1'):
        today = date.today()
        y = today.year if today.month >= ACADEMIC_YEAR_START_MONTH else today.year - 1
        autumn_start, autumn_end = date(y, 9, 1), date(y + 1, 1, 15)
        spring_start, spring_end = date(y + 1, 2, 1), date(y + 1, 6, 30)
        autumn_current = 1 if today <= autumn_end else 0
        execute('INSERT INTO terms (label, start_date, end_date, is_current) '
                'VALUES (?,?,?,?)',
                (f'{y}-{str(y + 1)[2:]} 秋季', autumn_start.isoformat(),
                 autumn_end.isoformat(), autumn_current))
        execute('INSERT INTO terms (label, start_date, end_date, is_current) '
                'VALUES (?,?,?,?)',
                (f'{y}-{str(y + 1)[2:]} 春季', spring_start.isoformat(),
                 spring_end.isoformat(), 0 if autumn_current else 1))
    if not q('SELECT 1 FROM holidays LIMIT 1'):
        # 只放兩個明確的固定假日當示範；實際校曆由 /admin/terms 批次匯入
        y = date.today().year
        for d, label in ((date(y, 10, 1), '國慶日'), (date(y + 1, 1, 1), '元旦')):
            execute('INSERT INTO holidays (day, label) VALUES (?,?) '
                    'ON CONFLICT(day) DO NOTHING', (d.isoformat(), label))


if __name__ == '__main__':
    with app.app_context():
        init_db()
        created = seed()
        if created:
            print('已寫入示範資料。')
        seed_timetable()
        seed_calendar()
    # debug 模式（Werkzeug 除錯器／自動重載）預設關閉：誰都能 `python app.py`
    # 直接把服務跑起來，不該預設附帶一個可執行任意程式碼的除錯控制台。
    # 本機開發時用 LEAVE_DEBUG=1 開啟（start.bat 已預設設定）。
    app.run(host='127.0.0.1', port=5055,
            debug=os.environ.get('LEAVE_DEBUG', '0') == '1')
