# -*- coding: utf-8 -*-
"""冒煙測試：走完整鏈路，並覆蓋校驗、權限與安全防護。

每次執行都使用全新的暫存資料庫，不會污染 leave_system.db。
用法：python smoke_test.py
"""
import os
import io
import sys
import shutil
import sqlite3
import tempfile
import datetime

TMP = tempfile.mkdtemp(prefix='leave-smoke-')
os.environ['LEAVE_DB'] = os.path.join(TMP, 'test.db')
os.environ['LEAVE_UPLOAD_DIR'] = os.path.join(TMP, 'uploads')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import (app, calc_days, init_db, seed, academic_year_start,  # noqa: E402
                MAX_REASON_LEN, MAX_COMMENT_LEN, MAX_PHONE_LEN, _smtp_configured)

with app.app_context():
    init_db()
    seed()

# 業務流程測試期間關閉 CSRF（CSRF 本身另有專門測試）
app.config['CSRF_ENABLED'] = False
c = app.test_client()

TODAY = datetime.date.today()
PASS = 0


def check(label, cond):
    global PASS
    assert cond, f'FAILED: {label}'
    PASS += 1
    print(f'  ✓ {label}')


def login(u, password='Pass@123'):
    """登入。會先抓登入頁的 CSRF token，因此 CSRF 開關兩種狀態下都能用。"""
    html = c.get('/login').get_data(as_text=True)
    token = ''
    if 'name="csrf_token" value="' in html:
        token = html.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post('/login', data={'username': u, 'password': password, 'csrf_token': token})
    assert r.status_code == 302, f'login failed for {u}: {r.status_code}'
    assert c.get('/').status_code == 200, f'{u} 登入後仍進不了首頁（可能沒真的登入）'


def get(path):
    r = c.get(path)
    assert r.status_code == 200, f'GET {path} -> {r.status_code}'
    return r.get_data(as_text=True)


def submit(username, type_id, start, end, reason='事由', sp='AM', ep='PM', attach=None):
    """提交假單，回傳 (rid 或 None, 頁面 HTML)。"""
    login(username)
    data = {'leave_type_id': str(type_id), 'start_date': start.isoformat(),
            'end_date': end.isoformat(), 'start_period': sp, 'end_period': ep,
            'reason': reason, 'contact_phone': '13800000001'}
    if attach:
        data['attachment'] = (io.BytesIO(b'fake-proof'), attach)
    r = c.post('/new', data=data, content_type='multipart/form-data')
    html = r.get_data(as_text=True)
    loc = dict(r.headers).get('Location', '')
    if r.status_code == 302 and '/request/' in loc:
        return int(loc.rsplit('/', 1)[-1]), html
    # 被校驗擋下時會直接重渲染表單（200），錯誤訊息就在這個 HTML 裡
    return None, html


def db_query(sql, args=()):
    conn = sqlite3.connect(os.environ['LEAVE_DB'])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


print('\n[1] 天數計算')
check('同日 AM→PM = 1.0 天', calc_days(datetime.date(2026, 9, 23), datetime.date(2026, 9, 23), 'AM', 'PM') == 1.0)
check('同日 AM→AM = 0.5 天', calc_days(datetime.date(2026, 9, 23), datetime.date(2026, 9, 23), 'AM', 'AM') == 0.5)
check('跨 5 天 = 5.0 天', calc_days(datetime.date(2026, 9, 21), datetime.date(2026, 9, 25), 'AM', 'PM') == 5.0)
check('9/21 PM → 9/25 AM = 4.0 天', calc_days(datetime.date(2026, 9, 21), datetime.date(2026, 9, 25), 'PM', 'AM') == 4.0)
check('學年起始日為 9/1', academic_year_start().month == 9 and academic_year_start().day == 1)

print('\n[2] 提交 → 兩級審批 → 生效')
# 各測試使用互不重疊的未來區間，避免彼此干擾
D2 = TODAY + datetime.timedelta(days=10)
rid, _ = submit('2024003', 2, D2, D2 + datetime.timedelta(days=5),
                '住院治療', attach='proof.pdf')
check('6 天病假提交成功', rid is not None)
check('狀態為待導師審批', '待導師審批' in get(f'/request/{rid}'))

login('T001')
check('導師待辦清單含該單', str(rid) in get('/approvals'))
c.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '同意'})
check('導師核准後轉院系', '待院系審批' in get(f'/request/{rid}'))

login('F001')
check('院系待辦清單含該單', str(rid) in get('/approvals'))
c.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '核備'})
check('院系核准後生效', '已核准' in get(f'/request/{rid}'))

print('\n[3] 短假單：導師核准即生效')
D3 = TODAY + datetime.timedelta(days=20)
rid2, _ = submit('2024004', 1, D3, D3, '家中事務')
login('T002')   # 2024004 的導師是 T002
c.post(f'/request/{rid2}/decide', data={'action': 'approve', 'comment': 'OK'})
check('1 天事假導師核准即生效', '已核准' in get(f'/request/{rid2}'))

print('\n[4] 駁回與撤回')
D4 = TODAY + datetime.timedelta(days=25)
rid3, _ = submit('2024005', 1, D4, D4, '外出')
login('T002')
c.post(f'/request/{rid3}/decide', data={'action': 'reject', 'comment': '事由不清'})
check('駁回後狀態為已駁回', '已駁回' in get(f'/request/{rid3}'))

D5 = TODAY + datetime.timedelta(days=30)
rid4, _ = submit('2024005', 1, D5, D5, '事假')
c.post(f'/request/{rid4}/withdraw')
check('撤回後狀態為已撤回', '已撤回' in get(f'/request/{rid4}'))

print('\n[5] 表單校驗')
_, html = submit('2024003', 1, D2, D2 + datetime.timedelta(days=2), '與已核准假單重疊')
check('重疊區間被阻擋', '已有假單' in html)

_, html = submit('2024004', 1, D3 + datetime.timedelta(days=1),
                 D3 + datetime.timedelta(days=121), '天數過長')
check('超過 90 天被阻擋', '90' in html)

_, html = submit('2024004', 1, TODAY - datetime.timedelta(days=60),
                 TODAY - datetime.timedelta(days=55), '太久以前')
check('回溯超過 30 天被阻擋', '補請' in html)

_, html = submit('2024004', 1, TODAY + datetime.timedelta(days=5),
                 TODAY, '結束早於開始')
check('結束早於開始被阻擋', '不能早於' in html)

# 已駁回／已撤回的假單不佔用區間 → 同一學生可重新申請同一天
rid5, _ = submit('2024005', 1, D5, D5, '重新申請同一天')
check('已撤回的假單不佔用區間', rid5 is not None)

print('\n[6] 權限')
login('2024005')
r = c.post(f'/request/{rid2}/decide', data={'action': 'approve', 'comment': 'x'},
           follow_redirects=True)
check('學生嘗試審批被拒', '沒有' in r.get_data(as_text=True))

r = c.get('/stats', follow_redirects=True)
check('學生無法進入統計頁', '沒有權限' in r.get_data(as_text=True))

# 附件存取控制：非本人、非導師、非同院系者不該拿到病假證明
rows = db_query("SELECT attachment FROM leave_requests WHERE attachment<>'' LIMIT 1")
check('確實有附件寫入', len(rows) == 1)
att = rows[0]['attachment']
login('2024003')
check('本人可下載自己的附件', c.get(f'/uploads/{att}').status_code == 200)
login('2024005')
r = c.get(f'/uploads/{att}', follow_redirects=True)
check('他人下載附件被拒', '沒有權限' in r.get_data(as_text=True))
login('F001')
check('院系審批人可下載該附件', c.get(f'/uploads/{att}').status_code == 200)

print('\n[7] 帳號與密碼')
login('2024001')
check('修改密碼頁可達', '目前密碼' in get('/password'))
r = c.post('/password', data={'old_password': 'Pass@123', 'new_password': 'WrongConfirm',
                              'confirm_password': 'DifferentOne'})
check('兩次新密碼不一致被擋', '不一致' in r.get_data(as_text=True))
c.post('/password', data={'old_password': 'Pass@123', 'new_password': 'NewPass@2026',
                          'confirm_password': 'NewPass@2026'})
r = c.post('/login', data={'username': '2024001', 'password': 'NewPass@2026'})
check('新密碼可登入', r.status_code == 302)
# 改回來，避免影響後續測試
r = c.post('/login', data={'username': '2024001', 'password': 'NewPass@2026'})
c.post('/password', data={'old_password': 'NewPass@2026', 'new_password': 'Pass@123',
                          'confirm_password': 'Pass@123'})

login('admin')
uid = db_query("SELECT id FROM users WHERE username='2024002'")[0]['id']
r = c.post(f'/admin/users/{uid}/reset-password', data={'password': 'TempPass@99'},
           follow_redirects=True)
check('管理員可重設密碼', '已重設' in r.get_data(as_text=True))
r = c.post('/login', data={'username': '2024002', 'password': 'TempPass@99'})
check('重設後的密碼可登入', r.status_code == 302)
login('admin')
r = c.post(f'/admin/users/{uid}/edit', data={'name': '張子謙', 'department': '人工智能學域',
                                             'class_name': 'AI-1班', 'email': 'x@example.edu',
                                             'advisor_id': ''}, follow_redirects=True)
check('管理員可編輯帳號', '已更新' in r.get_data(as_text=True))

print('\n[8] CSRF 防護')
app.config['CSRF_ENABLED'] = True
login('2024001')
before = db_query('SELECT COUNT(*) c FROM leave_requests')[0]['c']
r = c.post('/new', data={'leave_type_id': '1',
                         'start_date': (TODAY + datetime.timedelta(days=50)).isoformat(),
                         'end_date': (TODAY + datetime.timedelta(days=50)).isoformat(),
                         'reason': 'no token'}, content_type='multipart/form-data')
after = db_query('SELECT COUNT(*) c FROM leave_requests')[0]['c']
check('缺少 CSRF token 的 POST 被拒且不寫入資料庫', after == before)

# 帶正確 token 則可通過
html = get('/new')
token = html.split('name="csrf_token" value="')[1].split('"')[0]
before = db_query('SELECT COUNT(*) c FROM leave_requests')[0]['c']
c.post('/new', data={'csrf_token': token, 'leave_type_id': '1',
                     'start_date': (TODAY + datetime.timedelta(days=40)).isoformat(),
                     'end_date': (TODAY + datetime.timedelta(days=40)).isoformat(),
                     'reason': 'with token'}, content_type='multipart/form-data')
after = db_query('SELECT COUNT(*) c FROM leave_requests')[0]['c']
check('帶 CSRF token 的 POST 可通過', after == before + 1)

print('\n[9] 登入限流')
app.config['CSRF_ENABLED'] = False
# 用 2024002 測試，避免把後續測試要用的帳號鎖住
for _ in range(12):
    c.post('/login', data={'username': '2024002', 'password': 'definitely-wrong'})
r = c.post('/login', data={'username': '2024002', 'password': 'TempPass@99'})
check('連續失敗後觸發限流', r.status_code == 429)

print('\n[10] 頁面可達性與分頁')
for u, paths in [('T001', ['/', '/stats', '/export', '/approvals?scope=all',
                           '/approvals?scope=all&page=2', '/notifications', '/password']),
                 ('admin', ['/admin/users', '/admin/users?role=student', '/admin/types',
                            '/password']),
                 ('2024001', ['/my', '/my?status=approved', '/my?page=1', '/new'])]:
    login(u)
    for p in paths:
        r = c.get(p)
        check(f'{u} GET {p} → 200', r.status_code == 200)

print('\n[11] 安全標頭')
r = c.get('/login')
h = r.headers
check('X-Content-Type-Options: nosniff', h.get('X-Content-Type-Options') == 'nosniff')
check('Referrer-Policy 已設定', bool(h.get('Referrer-Policy')))
check('Content-Security-Policy 已設定', bool(h.get('Content-Security-Policy')))

print('\n[12] 自由文字長度上限')
login('2024003')
long_reason = '事' * (MAX_REASON_LEN + 10)
r = c.post('/new', data={'leave_type_id': '1',
                         'start_date': (TODAY + datetime.timedelta(days=60)).isoformat(),
                         'end_date': (TODAY + datetime.timedelta(days=60)).isoformat(),
                         'reason': long_reason, 'contact_phone': '13800000001'},
            content_type='multipart/form-data')
check('事由超過上限被阻擋', '過長' in r.get_data(as_text=True))

long_phone = '1' * (MAX_PHONE_LEN + 10)
r = c.post('/new', data={'leave_type_id': '1',
                         'start_date': (TODAY + datetime.timedelta(days=61)).isoformat(),
                         'end_date': (TODAY + datetime.timedelta(days=61)).isoformat(),
                         'reason': '外出', 'contact_phone': long_phone},
            content_type='multipart/form-data')
check('聯絡電話超過上限被阻擋', '過長' in r.get_data(as_text=True))

rid_c, _ = submit('2024004', 1, TODAY + datetime.timedelta(days=62),
                  TODAY + datetime.timedelta(days=62), '外出')
login('T002')
long_comment = '意見' * (MAX_COMMENT_LEN + 10)
r = c.post(f'/request/{rid_c}/decide', data={'action': 'approve', 'comment': long_comment},
           follow_redirects=True)
check('審批意見超過上限被阻擋', '過長' in r.get_data(as_text=True))

print('\n[13] 通知「全部標為已讀」')
login('T001')
tid = db_query("SELECT id FROM users WHERE username='T001'")[0]['id']
before_unread = db_query('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0', (tid,))[0]['c']
check('T001 有未讀通知', before_unread > 0)
html = get('/notifications')
check('通知頁含「全部標為已讀」按鈕', '全部標為已讀' in html)
r = c.post('/notifications/read', data={}, follow_redirects=False)
check('POST 全部標為已讀導向回通知頁',
      r.status_code == 302 and '/notifications' in dict(r.headers).get('Location', ''))
after_unread = db_query('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0', (tid,))[0]['c']
check('標為已讀後無未讀', after_unread == 0)

print('\n[14] Email 通道預設關閉（失敗靜默）')
check('未設定 LEAVE_SMTP_* → 不啟用 Email 通道', not _smtp_configured())

shutil.rmtree(TMP, ignore_errors=True)
print(f'\nALL {PASS} SMOKE CHECKS PASSED')
