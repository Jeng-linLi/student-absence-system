# -*- coding: utf-8 -*-
"""v1.1.0 測試：交易原子化、待辦分頁、referrer 開放重定向防護、
SMTP 逾時、重複審批守衛、撤回院級通知、單則通知已讀、電話格式校驗。

沿用 conftest 的共用臨時資料庫與種子資料。為了不與其他測試檔搶帳號，
本檔自建專用學生（v11_stu 掛 T002、v11_stu_a 掛 T001），登入與提交都走它們。
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, execute_guard, todo_requests, send_email, transaction  # noqa: E402
from utils import valid_phone  # noqa: E402

TODAY = datetime.date.today()
PW = 'V11test@2026'
STU = 'v11_stu'            # 導師 T002 的學生
STU_A = 'v11_stu_a'        # 導師 T001 的學生


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def students():
    """自建兩名學生：STU 掛 T002、STU_A 掛 T001（已存在則沿用）。"""
    t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
    t2 = q("SELECT id FROM users WHERE username='T002'", one=True)['id']
    admin = app.test_client()
    admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
    for name, adv in ((STU, t2), (STU_A, t1)):
        if not q('SELECT id FROM users WHERE username=?', (name,), one=True):
            r = admin.post('/admin/users/add', data={
                'username': name, 'password': PW, 'name': f'v11 測試生 {name}',
                'role': 'student', 'department': '人工智能學域',
                'email': f'{name}@example.edu',
                'advisor_id': str(adv)})
            assert r.status_code == 200, f'建立 {name} 失敗'
    return {'t1': t1, 't2': t2,
            'stu': q('SELECT id FROM users WHERE username=?', (STU,), one=True)['id'],
            'a': q('SELECT id FROM users WHERE username=?', (STU_A,), one=True)['id']}


def _login(client, username, password=PW):
    return client.post('/login', data={'username': username, 'password': password})


def _submit(client, day_offset, reason='v11 測試', days=1):
    """以已登入學生身份提交一張假單，回傳 rid。"""
    d = TODAY + datetime.timedelta(days=day_offset)
    r = client.post('/new', data={'leave_type_id': '1',
                                  'start_date': d.isoformat(),
                                  'end_date': (d + datetime.timedelta(days=days - 1)).isoformat(),
                                  'start_period': 'AM', 'end_period': 'PM',
                                  'reason': reason, 'contact_phone': '13800000001'},
                    content_type='multipart/form-data')
    assert r.status_code == 302, f'提交失敗：{r.status_code}'
    return int(r.headers['Location'].rsplit('/', 1)[-1])


# --------------------------------------------------------------------------
# 交易原子化（v1.1.0）：多寫入流程要麼全部生效、要麼全部不發生
# --------------------------------------------------------------------------
def test_transaction_atomicity():
    before = q('SELECT COUNT(*) c FROM notifications', one=True)['c']
    # 例外 → 整體回滾，不留半套狀態
    try:
        with transaction():
            execute('INSERT INTO notifications (user_id, message) VALUES (?,?)', (1, 'tx rollback'))
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    assert q('SELECT COUNT(*) c FROM notifications', one=True)['c'] == before
    # 成功 → 一次提交
    with transaction():
        execute('INSERT INTO notifications (user_id, message) VALUES (?,?)', (1, 'tx commit'))
    assert q('SELECT COUNT(*) c FROM notifications', one=True)['c'] == before + 1
    # 巢狀區塊沿用外層交易：內層成功、外層失敗 → 全部回滾
    try:
        with transaction():
            execute('INSERT INTO notifications (user_id, message) VALUES (?,?)', (1, 'tx outer'))
            with transaction():
                execute('INSERT INTO notifications (user_id, message) VALUES (?,?)', (1, 'tx inner'))
            raise RuntimeError('boom2')
    except RuntimeError:
        pass
    assert q('SELECT COUNT(*) c FROM notifications', one=True)['c'] == before + 1
    # 收尾清理
    execute("DELETE FROM notifications WHERE message LIKE 'tx %'")


def test_execute_guard_rowcount():
    """execute_guard：條件沒匹配上時影響 0 列（併發守衛的基礎）。"""
    n = execute_guard('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?',
                      (-1, -1))
    assert n == 0
    # 有匹配時如實回傳列數
    rid = execute('INSERT INTO notifications (user_id, message) VALUES (?,?)', (1, 'guard probe'))
    try:
        assert execute_guard('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?',
                             (rid, 1)) == 1
        # 已讀後再條件更新（is_read=0）→ 0 列
        assert execute_guard('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=? '
                             'AND is_read=0', (rid, 1)) == 0
    finally:
        execute('DELETE FROM notifications WHERE id=?', (rid,))


# --------------------------------------------------------------------------
# 待辦清單分頁（v1.1.0）：此前 todo 視圖一次載入全部待審假單
# --------------------------------------------------------------------------
def test_todo_requests_limit_offset():
    """limit/offset 語義：與完整清單切片比對（以明確 created_at 保證排序確定）。"""
    t2 = q("SELECT * FROM users WHERE username='T002'", one=True)
    # 不依賴 students fixture：STU 不存在就改用 T002 的種子學生 2024004
    stu = q('SELECT id FROM users WHERE username=?', (STU,), one=True)
    sid = stu['id'] if stu else q("SELECT id FROM users WHERE username='2024004'",
                                  one=True)['id']
    for i in range(8):
        execute('INSERT INTO leave_requests (student_id, leave_type_id, start_date, end_date, '
                'start_period, end_period, days, reason, status, current_level, required_level, '
                'created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (sid, 1, '2026-02-01', '2026-02-01', 'AM', 'PM', 1.0,
                 f'v11 單元 {i}', 'pending', 1, 1, f'2026-01-01 10:00:{i:02d}'))
    try:
        all_rows = todo_requests(t2)
        assert len(all_rows) >= 8
        page1 = todo_requests(t2, limit=5)
        assert [r['id'] for r in page1] == [r['id'] for r in all_rows[:5]]
        page2 = todo_requests(t2, limit=5, offset=5)
        assert [r['id'] for r in page2] == [r['id'] for r in all_rows[5:10]]
    finally:
        execute("DELETE FROM approvals WHERE request_id IN "
                "(SELECT id FROM leave_requests WHERE reason LIKE 'v11 單元%')")
        execute("DELETE FROM leave_requests WHERE reason LIKE 'v11 單元%'")


def test_approvals_todo_pagination(students):
    """待辦清單分頁：超過一頁時顯示頁碼、第二頁可開、越界頁碼收斂。"""
    sid = students['stu']
    for i in range(25):
        d = TODAY + datetime.timedelta(days=400 + i)
        rid = execute('INSERT INTO leave_requests (student_id, leave_type_id, start_date, '
                      'end_date, start_period, end_period, days, reason, status, '
                      'current_level, required_level) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (sid, 1, d.isoformat(), d.isoformat(), 'AM', 'PM', 1.0,
                       f'v11 分頁 {i:02d}', 'pending', 1, 1))
        execute('INSERT INTO approvals (request_id, level, action) VALUES (?,?,?)', (rid, 1, 'pending'))
    expected = q("SELECT COUNT(*) c FROM leave_requests r JOIN users s ON s.id=r.student_id "
                 "WHERE r.status='pending' AND r.current_level=1 AND s.advisor_id=?",
                 (students['t2'],), one=True)['c']
    assert expected > 20
    t = app.test_client()
    assert _login(t, 'T002', 'Demo@2026').status_code == 302
    html = t.get('/approvals').get_data(as_text=True)
    assert f'第 1 / {(expected + 19) // 20} 頁' in html
    assert f'共 {expected} 筆' in html
    assert t.get('/approvals?page=2').status_code == 200
    assert t.get('/approvals?page=999').status_code == 200      # 越界收斂到末頁，不是 404
    # 全部假單視圖的分頁不受影響
    assert t.get('/approvals?scope=all').status_code == 200
    # 收尾清理
    execute("DELETE FROM approvals WHERE request_id IN "
            "(SELECT id FROM leave_requests WHERE reason LIKE 'v11 分頁%')")
    execute("DELETE FROM notifications WHERE request_id IN "
            "(SELECT id FROM leave_requests WHERE reason LIKE 'v11 分頁%')")
    execute("DELETE FROM leave_requests WHERE reason LIKE 'v11 分頁%'")


# --------------------------------------------------------------------------
# referrer 開放重定向防護（v1.1.0）
# --------------------------------------------------------------------------
def test_referrer_not_open_redirect():
    """400／413 的導回目標不可被外部 referrer 操縱。"""
    c = app.test_client()
    app.config['CSRF_ENABLED'] = True
    try:
        # 外部 referrer → 導去 dashboard，不會把使用者送往釣魚站
        # （400 errorhandler 以 redirect 回應，因此外層狀態碼是 302）
        r = c.post('/login', data={'username': 'x', 'password': 'y'},
                   headers={'Referer': 'http://evil.example.com/phish'})
        assert r.status_code == 302
        loc = r.headers.get('Location', '')
        assert 'evil.example.com' not in loc
        assert loc.endswith('/')                       # dashboard
        # 同站 referrer 仍沿用（不破壞原本的「導回原頁面」體驗）
        r2 = c.post('/login', data={'username': 'x', 'password': 'y'},
                    headers={'Referer': 'http://localhost/login'})
        assert r2.status_code == 302
        assert r2.headers['Location'] == 'http://localhost/login'
    finally:
        app.config['CSRF_ENABLED'] = False


# --------------------------------------------------------------------------
# SMTP 連線逾時（v1.1.0）：郵件伺服器不可達時不把 worker 拖死
# --------------------------------------------------------------------------
def test_smtp_uses_timeout(monkeypatch):
    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured['timeout'] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, *a):
            pass

    monkeypatch.setattr('smtplib.SMTP', _FakeSMTP)
    os.environ.update({'LEAVE_SMTP_HOST': 'smtp.test.invalid',
                       'LEAVE_SMTP_USER': 'u', 'LEAVE_SMTP_PASS': 'p',
                       'LEAVE_SMTP_FROM': 'f@test.invalid'})
    try:
        send_email('to@test.invalid', 'subject', 'body')
        assert captured['timeout'] == 10                 # 預設 10 秒
        os.environ['LEAVE_SMTP_TIMEOUT'] = '3'
        send_email('to@test.invalid', 'subject', 'body')
        assert captured['timeout'] == 3                  # 可用環境變數覆寫
    finally:
        for k in ('LEAVE_SMTP_HOST', 'LEAVE_SMTP_USER', 'LEAVE_SMTP_PASS',
                  'LEAVE_SMTP_FROM', 'LEAVE_SMTP_TIMEOUT'):
            os.environ.pop(k, None)


# --------------------------------------------------------------------------
# 重複審批守衛（v1.1.0）：雙擊／併發點擊不覆寫第一位審批人的結果
# --------------------------------------------------------------------------
def test_approvals_guard_rejects_processed(students):
    """approvals 更新敘述的 pending 守衛：已被處理的審批列影響 0 列。"""
    c = app.test_client()
    _login(c, STU)
    rid = _submit(c, 321, 'v11 併發守衛測試')
    # 模擬「另一位審批人剛好處理完」：把該級 approvals 改成非 pending
    execute("UPDATE approvals SET action='approve' WHERE request_id=? AND level=1", (rid,))
    n = execute_guard("UPDATE approvals SET action=?, approver_id=?, comment=?, acted_at=? "
                      "WHERE request_id=? AND level=? AND action='pending'",
                      ('reject', 1, 'x', '2026-01-01 00:00:00', rid, 1))
    assert n == 0
    # 仍為 pending 的列則正常影響 1 列
    rid2 = _submit(c, 322, 'v11 併發守衛測試 2')
    n2 = execute_guard("UPDATE approvals SET action=?, approver_id=?, comment=?, acted_at=? "
                       "WHERE request_id=? AND level=? AND action='pending'",
                       ('reject', 1, 'x', '2026-01-01 00:00:00', rid2, 1))
    assert n2 == 1
    # 收尾清理
    for tbl in ('approvals', 'audit_logs', 'notifications'):
        execute(f'DELETE FROM {tbl} WHERE request_id IN (?,?)', (rid, rid2))
    execute('DELETE FROM leave_requests WHERE id IN (?,?)', (rid, rid2))


def test_double_decide_via_http(students):
    """端到端：核准後再 POST decide，被明確擋下且不會重複通知。"""
    c = app.test_client()
    _login(c, STU)
    rid = _submit(c, 323, 'v11 重複審批測試')
    t = app.test_client()
    assert _login(t, 'T002', 'Demo@2026').status_code == 302
    assert t.post(f'/request/{rid}/decide',
                  data={'action': 'approve', 'comment': '同意'}).status_code == 302
    r2 = t.post(f'/request/{rid}/decide',
                data={'action': 'reject', 'comment': '再來一次'}, follow_redirects=True)
    assert '無法再操作' in r2.get_data(as_text=True)
    # 狀態未被第二次操作改動，學生只收到一則核准通知
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,), one=True)['status'] == 'approved'
    rows = q('SELECT message FROM notifications WHERE user_id=? AND request_id=?',
             (students['stu'], rid))
    assert sum('已核准' in row['message'] for row in rows) == 1


# --------------------------------------------------------------------------
# 撤回院系級假單時通知院系審批人（v1.1.0）
# --------------------------------------------------------------------------
def test_withdraw_level2_notifies_faculty(students):
    c = app.test_client()
    _login(c, STU)
    rid = _submit(c, 324, 'v11 撤回院系通知', days=6)      # 6 天 → 需院系複核
    t = app.test_client()
    assert _login(t, 'T002', 'Demo@2026').status_code == 302
    t.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '同意'})
    assert q('SELECT current_level FROM leave_requests WHERE id=?', (rid,),
             one=True)['current_level'] == 2
    c.post(f'/request/{rid}/withdraw')
    f1 = q("SELECT id FROM users WHERE username='F001'", one=True)['id']
    rows = q('SELECT message FROM notifications WHERE user_id=? AND request_id=?', (f1, rid))
    assert any('撤回' in row['message'] for row in rows), '撤回院系級假單未通知院辦'
    # 重複撤回被擋（狀態已非 pending）
    r2 = c.post(f'/request/{rid}/withdraw', follow_redirects=True)
    assert '已結案' in r2.get_data(as_text=True)


# --------------------------------------------------------------------------
# 單則通知標為已讀（v1.1.0）
# --------------------------------------------------------------------------
def test_notification_mark_single(students):
    c = app.test_client()
    _login(c, STU)
    rid = _submit(c, 325, 'v11 單則已讀測試')
    row = q('SELECT id FROM notifications WHERE user_id=? AND request_id=? AND is_read=0 '
            'ORDER BY id DESC', (students['t2'], rid), one=True)
    assert row, '導師未收到提交通知'
    nid = row['id']

    # 學生標記他人的通知：標不到，也不透存在性
    s = app.test_client()
    _login(s, STU_A)
    r = s.post(f'/notifications/{nid}/read', follow_redirects=True)
    assert '找不到該通知' in r.get_data(as_text=True)
    assert q('SELECT is_read FROM notifications WHERE id=?', (nid,), one=True)['is_read'] == 0

    # 本人可以標為已讀
    t = app.test_client()
    _login(t, 'T002', 'Demo@2026')
    assert t.post(f'/notifications/{nid}/read').status_code == 302
    assert q('SELECT is_read FROM notifications WHERE id=?', (nid,), one=True)['is_read'] == 1

    # 重複標記：友善提示而不是 500
    r2 = t.post(f'/notifications/{nid}/read', follow_redirects=True)
    assert '找不到該通知' in r2.get_data(as_text=True)


# --------------------------------------------------------------------------
# 聯絡電話格式校驗（v1.1.0）
# --------------------------------------------------------------------------
def test_valid_phone():
    assert valid_phone('')                              # 非必填，空值合法
    assert valid_phone('13800000001')
    assert valid_phone('+86 (138) 000-0001')
    assert not valid_phone('not-a-phone')
    assert not valid_phone('0932-123-456 ext 5')        # 含字母
    assert not valid_phone('1' * 31)                    # 超過 MAX_PHONE_LEN


def test_new_request_rejects_bad_phone(students):
    c = app.test_client()
    _login(c, STU)
    d = TODAY + datetime.timedelta(days=326)
    r = c.post('/new', data={'leave_type_id': '1',
                             'start_date': d.isoformat(), 'end_date': d.isoformat(),
                             'start_period': 'AM', 'end_period': 'PM',
                             'reason': 'v11 電話格式', 'contact_phone': 'call-me-maybe'},
               content_type='multipart/form-data')
    assert '格式不合法' in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) c FROM leave_requests WHERE reason='v11 電話格式'",
             one=True)['c'] == 0
    # 合法格式仍可提交
    rid = _submit(c, 327, 'v11 電話合法')
    assert rid > 0


# --------------------------------------------------------------------------
# 學生／院系帳號必須填寫院系（v1.1.0）：空院系的學生遞交兩級假單後，
# 沒有任何院系審批人看得見它——整張單卡在第二級沒人處理。
# --------------------------------------------------------------------------
def test_add_user_student_requires_department():
    admin = app.test_client()
    assert _login(admin, 'admin', 'Demo@2026').status_code == 302
    r = admin.post('/admin/users/add', data={'username': 'nodept_u', 'password': 'GoodPass9',
                                             'name': '無院系', 'role': 'student'},
                   follow_redirects=True)
    assert '必須填寫所屬院系' in r.get_data(as_text=True)
    assert q("SELECT id FROM users WHERE username='nodept_u'", one=True) is None
    # 導師／管理員不強制（他們不依院系匹配審批範圍）
    r2 = admin.post('/admin/users/add', data={'username': 'nodept_t', 'password': 'GoodPass9',
                                              'name': '無院系導師', 'role': 'advisor'},
                    follow_redirects=True)
    assert '必須填寫所屬院系' not in r2.get_data(as_text=True)
    assert q("SELECT id FROM users WHERE username='nodept_t'", one=True) is not None


def test_edit_user_student_cannot_clear_department(students):
    admin = app.test_client()
    assert _login(admin, 'admin', 'Demo@2026').status_code == 302
    r = admin.post(f"/admin/users/{students['stu']}/edit",
                   data={'name': 'v11 測試生', 'department': ''},
                   follow_redirects=True)
    assert '必須填寫所屬院系' in r.get_data(as_text=True)
    # 院系未被清空
    assert q('SELECT department FROM users WHERE id=?', (students['stu'],),
             one=True)['department'] == '人工智能學域'
