# -*- coding: utf-8 -*-
"""
線上請假系統（原型）
學生提交 → 導師審批 → （必要時）院系審批 → 核准
"""
import os
import csv
import io
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, session,
                   g, flash, jsonify, send_from_directory, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'leave_system.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 審批分流門檻：超過此天數需院系複核
FACULTY_THRESHOLD_DAYS = 3
ALLOWED_EXT = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

app = Flask(__name__)
app.secret_key = os.environ.get('LEAVE_SECRET', 'dev-only-change-me-in-production')
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# 資料庫
# --------------------------------------------------------------------------
def get_db():
    if 'db' not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def init_db():
    with open(os.path.join(BASE_DIR, 'schema.sql'), encoding='utf-8') as f:
        get_db().executescript(f.read())
    get_db().commit()


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def parse_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def calc_days(start_date, end_date, start_period, end_period):
    """按半天粒度計算請假天數。"""
    if end_date < start_date:
        return 0
    full = (end_date - start_date).days - 1          # 中間的完整天
    head = 0.5 if start_period == 'PM' else 1.0
    tail = 0.5 if end_period == 'AM' else 1.0
    if start_date == end_date:
        if start_period == 'AM' and end_period == 'PM':
            return 1.0
        return 0.5
    return max(full, 0) + head + tail


def required_level_for(days, leave_type):
    if leave_type and leave_type['needs_faculty']:
        return 2
    return 2 if days > FACULTY_THRESHOLD_DAYS else 1


def notify(user_id, request_id, message):
    if user_id:
        execute('INSERT INTO notifications (user_id, request_id, message) VALUES (?,?,?)',
                (user_id, request_id, message))


def log_action(request_id, actor_id, action, detail=''):
    execute('INSERT INTO audit_logs (request_id, actor_id, action, detail) VALUES (?,?,?,?)',
            (request_id, actor_id, action, detail))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def current_user():
    if 'uid' not in session:
        return None
    return q('SELECT * FROM users WHERE id=? AND active=1', (session['uid'],), one=True)


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
    return dict(current_user=u, unread_count=unread,
                now=datetime.now(), FACULTY_THRESHOLD_DAYS=FACULTY_THRESHOLD_DAYS)


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
                             STATUS_LABEL=STATUS_LABEL)


# --------------------------------------------------------------------------
# 登入 / 登出
# --------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = q('SELECT * FROM users WHERE username=? AND active=1', (username,), one=True)
        if user and check_password_hash(user['password_hash'], password):
            session['uid'] = user['id']
            return redirect(url_for('dashboard'))
        flash('帳號或密碼錯誤。', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


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
    ctx = {'pending_mine': 0, 'todo': 0, 'recent': [], 'stats': {}, 'my_leave_days': 0}

    if u['role'] == 'student':
        ctx['pending_mine'] = q('SELECT COUNT(*) c FROM leave_requests WHERE student_id=? '
                                "AND status='pending'", (u['id'],), one=True)['c']
        ctx['my_leave_days'] = q("SELECT COALESCE(SUM(days),0) s FROM leave_requests "
                                 "WHERE student_id=? AND status='approved'", (u['id'],), one=True)['s']
        ctx['recent'] = q('SELECT r.*, t.name type_name FROM leave_requests r '
                          'JOIN leave_types t ON t.id=r.leave_type_id '
                          'WHERE r.student_id=? ORDER BY r.created_at DESC LIMIT 5', (u['id'],))
    else:
        ctx['todo'] = count_todo(u)
        ctx['recent'] = todo_requests(u, limit=5)

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
    sql = ('SELECT r.*, t.name type_name FROM leave_requests r '
           'JOIN leave_types t ON t.id=r.leave_type_id WHERE r.student_id=?')
    args = [u['id']]
    if status:
        sql += ' AND r.status=?'
        args.append(status)
    sql += ' ORDER BY r.created_at DESC'
    return render_template('my_requests.html',
                           requests=q(sql, args), filter_status=status)


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

        err = None
        if not ltype:
            err = '請選擇有效的假別。'
        elif end_date < start_date:
            err = '結束日期不能早於開始日期。'
        elif days <= 0:
            err = '請假天數計算異常，請檢查日期與上午/下午設定。'
        elif not reason:
            err = '請填寫請假事由。'
        if err:
            flash(err, 'error')
            return render_template('new_request.html', types=types, form=request.form)

        attachment = ''
        file = request.files.get('attachment')
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('證明文件格式不支援（僅 PDF/圖片/Word）。', 'error')
                return render_template('new_request.html', types=types, form=request.form)
            fn = secure_filename(f"{u['username']}_{datetime.now():%Y%m%d%H%M%S}_{file.filename}")
            file.save(os.path.join(UPLOAD_DIR, fn))
            attachment = fn
        if ltype['requires_attachment'] and not attachment:
            flash(f"「{ltype['name']}」必須上傳證明文件。", 'error')
            return render_template('new_request.html', types=types, form=request.form)

        level = required_level_for(days, ltype)
        rid = execute(
            'INSERT INTO leave_requests (student_id, leave_type_id, start_date, end_date, '
            'start_period, end_period, days, reason, attachment, contact_phone, '
            'status, current_level, required_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (u['id'], type_id, start_date.isoformat(), end_date.isoformat(), sp, ep,
             days, reason, attachment, phone, 'pending', 1, level))
        execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 1, 'pending'))
        if level == 2:
            execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 2, 'pending'))

        log_action(rid, u['id'], 'submit', f"提交{ltype['name']} {days}天，審批層級{level}")
        if u['advisor_id']:
            notify(u['advisor_id'], rid, f"{u['name']} 提交了 {days} 天{ltype['name']}，待你審批。")
        flash('假單已提交，等待導師審批。', 'success')
        return redirect(url_for('detail', rid=rid))

    return render_template('new_request.html', types=types, form={})


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

    return render_template('request_detail.html', req=req, approvals=approvals,
                           logs=logs, can_approve=can_approve)


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
    if scope == 'all':
        rows = q('SELECT r.*, t.name type_name, s.name student_name, s.class_name, s.department '
                 'FROM leave_requests r '
                 'JOIN leave_types t ON t.id=r.leave_type_id '
                 'JOIN users s ON s.id=r.student_id '
                 'ORDER BY r.created_at DESC LIMIT 200')
    else:
        rows = todo_requests(u)
    return render_template('approvals.html', requests=rows, scope=scope)


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
@app.route('/notifications')
@login_required
def notifications():
    u = current_user()
    rows = q('SELECT n.*, r.start_date FROM notifications n '
             'LEFT JOIN leave_requests r ON r.id=n.request_id '
             'WHERE n.user_id=? ORDER BY n.created_at DESC, n.id DESC LIMIT 50', (u['id'],))
    execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (u['id'],))
    get_db().commit()
    return render_template('notifications.html', items=rows)


# --------------------------------------------------------------------------
# 學生請假統計（導師/院系/管理員）
# --------------------------------------------------------------------------
@app.route('/stats')
@login_required
@role_required('advisor', 'faculty', 'admin')
def stats():
    u = current_user()
    where, args = '', []
    if u['role'] == 'advisor':
        where = 'WHERE s.advisor_id=?'
        args = [u['id']]
    elif u['role'] == 'faculty':
        where = 'WHERE s.department=?'
        args = [u['department']]
    rows = q(f'SELECT s.id, s.name, s.username, s.class_name, s.department, '
             f'COUNT(r.id) total_cnt, '
             f"COALESCE(SUM(CASE WHEN r.status='approved' THEN r.days ELSE 0 END),0) approved_days, "
             f"COALESCE(SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END),0) pending_cnt, "
             f"COALESCE(SUM(CASE WHEN r.status='rejected' THEN 1 ELSE 0 END),0) rejected_cnt "
             f'FROM users s LEFT JOIN leave_requests r ON r.student_id=s.id '
             f"{where} GROUP BY s.id HAVING total_cnt>0 ORDER BY approved_days DESC", args)

    by_type = q("SELECT t.name, COUNT(r.id) cnt, COALESCE(SUM(r.days),0) days "
                'FROM leave_requests r JOIN leave_types t ON t.id=r.leave_type_id '
                "WHERE r.status='approved' GROUP BY t.id ORDER BY days DESC")
    return render_template('stats.html', rows=rows, by_type=by_type)


@app.route('/export')
@login_required
@role_required('advisor', 'faculty', 'admin')
def export_csv():
    u = current_user()
    where, args = '', []
    if u['role'] == 'advisor':
        where = 'WHERE s.advisor_id=?'
        args = [u['id']]
    elif u['role'] == 'faculty':
        where = 'WHERE s.department=?'
        args = [u['department']]
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
        w.writerow([r['id'], r['username'], r['name'], r['class_name'], r['department'],
                    r['type_name'], r['start_date'], r['end_date'], r['days'],
                    r['reason'], STATUS_LABEL.get(r['status'], r['status']),
                    r['current_level'], r['created_at']])
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             f'attachment; filename=leave_{date.today():%Y%m%d}.csv'})


# --------------------------------------------------------------------------
# 管理後台
# --------------------------------------------------------------------------
@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    role = request.args.get('role', '')
    sql = ('SELECT u.*, a.name advisor_name FROM users u '
           'LEFT JOIN users a ON a.id=u.advisor_id')
    args = []
    if role:
        sql += ' WHERE u.role=?'
        args.append(role)
    sql += ' ORDER BY u.role, u.username'
    advisors = q("SELECT id, name FROM users WHERE role='advisor' AND active=1 ORDER BY name")
    return render_template('admin_users.html', users=q(sql, args),
                           advisors=advisors, filter_role=role)


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
    advisor_id = request.form.get('advisor_id') or None
    password = request.form.get('password') or 'Pass@123'

    if not username or not name:
        flash('帳號與姓名必填。', 'error')
        return redirect(url_for('admin_users'))
    if q('SELECT id FROM users WHERE username=?', (username,), one=True):
        flash(f'帳號 {username} 已存在。', 'error')
        return redirect(url_for('admin_users'))
    execute('INSERT INTO users (username, password_hash, name, role, department, '
            'class_name, email, advisor_id) VALUES (?,?,?,?,?,?,?,?)',
            (username, generate_password_hash(password), name, role,
             department, class_name, email, advisor_id))
    flash(f'已建立帳號 {username}（初始密碼：{password}）', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def admin_toggle_user(uid):
    execute('UPDATE users SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?', (uid,))
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


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# --------------------------------------------------------------------------
# 種子資料
# --------------------------------------------------------------------------
def seed():
    if q('SELECT COUNT(*) c FROM users', one=True)['c']:
        return False
    pw = generate_password_hash('Pass@123')
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
    return True


@app.cli.command('init-db')
def init_db_command():
    init_db()
    print('資料庫已初始化。')


if __name__ == '__main__':
    with app.app_context():
        init_db()
        created = seed()
        if created:
            print('已寫入示範資料。')
    app.run(host='127.0.0.1', port=5055, debug=True)
