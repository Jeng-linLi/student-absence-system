# -*- coding: utf-8 -*-
"""v1.18.0 測試：把殘留的「假防護」補成真的，並關閉 D3 細粒度稽核。

守住幾件事：
  1. 課表預覽的日期區間有上限、時段有白名單（GET 不受 CSRF 保護）；
  2. 校曆檔與假日批次匯入是原子交易——失敗時畫面說「資料庫未變更」
     就必須真的沒變更；
  3. Email 驗證的一次性 token 守衛真的會擋下併發重複兌換；
  4. 額度設定不接受 NaN／Infinity（舊版會讓該假別的額度靜默失效）；
  5. 單張假單詳情頁對非當事人的讀取逐筆留 view_pii 稽核，
     學生看自己的單不留痕，且稽核明細不抄個資。

沿用 conftest 的 session 級共用臨時資料庫；自建帳號與專用學期避免
與其他測試檔互相污染。
"""
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app as app_module  # noqa: E402
import db as db_module  # noqa: E402
from app import (app, q, execute, affected_courses_for,  # noqa: E402
                 AUDIT_ACTION_LABEL)
from utils import APP_VERSION, MAX_AFFECTED_DAYS  # noqa: E402

DEMO = 'Demo@2026'
PW = 'V25test@2026'
STU = 'v25_stu'
TERM_LABEL = 'v25 測試學期'
HOLIDAY_A = '2099-12-30'
HOLIDAY_B = '2099-12-31'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def admin_client():
    c = app.test_client()
    r = c.post('/login', data={'username': 'admin', 'password': DEMO})
    assert r.status_code in (302, 200)
    return c


@pytest.fixture()
def make_student(admin_client):
    """建立專用學生（導師 T001），回傳 (username, password)。"""
    def _make(username=STU):
        t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
        if not q('SELECT id FROM users WHERE username=?', (username,), one=True):
            r = admin_client.post('/admin/users/add', data={
                'username': username, 'password': PW, 'name': f'{username} 測試生',
                'role': 'student', 'department': '人工智能學域',
                'email': f'{username}@example.edu', 'advisor_id': str(t1)})
            assert r.status_code == 200, f'建立 {username} 失敗'
        return username, PW
    return _make


def _uid(username):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _login(c, username, password=DEMO):
    return c.post('/login', data={'username': username, 'password': password})


def _count_view_pii(actor_id, rid=None):
    sql = "SELECT COUNT(*) c FROM audit_logs WHERE action='view_pii' AND actor_id=?"
    args = [actor_id]
    if rid is not None:
        sql += ' AND request_id=?'
        args.append(rid)
    return q(sql, args, one=True)['c']


# --------------------------------------------------------------------------
# 1. 課表預覽：區間上限與時段白名單
# --------------------------------------------------------------------------
def test_affected_courses_rejects_huge_range_without_touching_db(monkeypatch):
    """超大區間必須在查課表之前就回 []，不能進逐日迴圈。"""
    calls = {'n': 0}
    real_q = app_module.q

    def _counting_q(*a, **kw):
        calls['n'] += 1
        return real_q(*a, **kw)

    monkeypatch.setattr(app_module, 'q', _counting_q)
    assert affected_courses_for(1, '2000-01-01', '2099-12-31') == []
    assert calls['n'] == 0, '超限區間不該查詢課表／假日／補課日'


def test_affected_courses_rejects_huge_reversed_range():
    """起訖顛倒時會先對調再算天數，不能靠反向區間繞過上限。"""
    assert affected_courses_for(1, '2099-12-31', '2000-01-01') == []


def test_affected_courses_rejects_bad_period():
    assert affected_courses_for(1, '2026-11-02', '2026-11-03', 'XX', 'PM') == []
    assert affected_courses_for(1, '2026-11-02', '2026-11-03', 'AM', 'PM ') == []


def test_affected_courses_rejects_bad_date():
    assert affected_courses_for(1, 'not-a-date', '2026-11-03') == []


def test_affected_courses_boundary_still_works(monkeypatch):
    """上限當天仍可預覽；只是不該無上限。"""
    calls = {'n': 0}
    real_q = app_module.q

    def _counting_q(*a, **kw):
        calls['n'] += 1
        return real_q(*a, **kw)

    monkeypatch.setattr(app_module, 'q', _counting_q)
    start = date(2026, 11, 2)
    end = date.fromordinal(start.toordinal() + MAX_AFFECTED_DAYS)
    assert isinstance(affected_courses_for(1, start.isoformat(), end.isoformat()), list)
    assert calls['n'] >= 1, '未超限的區間應照常查課表'


def test_api_affected_courses_huge_range_is_empty(make_student):
    """學生單一 GET 打超大區間：即時回空陣列，不是 500 也不是長時間運算。"""
    uname, pw = make_student('v25_api_stu')
    c = app.test_client()
    _login(c, uname, pw)
    r = c.get('/api/affected_courses?start_date=2000-01-01&end_date=2099-12-31')
    assert r.status_code == 200
    assert r.get_json() == []
    # 時段白名單也要生效
    r2 = c.get('/api/affected_courses?start_date=2026-11-02&end_date=2026-11-03'
               '&start_period=EVENTING')
    assert r2.get_json() == []


def test_api_affected_courses_non_student_is_empty():
    c = app.test_client()
    _login(c, 'T001')
    assert c.get('/api/affected_courses?start_date=2026-11-02'
                 '&end_date=2026-11-03').get_json() == []


# --------------------------------------------------------------------------
# 2. 匯入交易：失敗時「資料庫未變更」必須為真
# --------------------------------------------------------------------------
def test_calendar_import_rolls_back_on_partial_failure(admin_client, monkeypatch):
    """第 3 次寫入拋錯時，前兩筆學期與稽核都不能留下。"""
    real_execute = db_module.execute
    calls = {'n': 0}

    def _flaky(sql, args=()):
        calls['n'] += 1
        if calls['n'] == 3:
            raise RuntimeError('模擬寫入中斷')
        return real_execute(sql, args)

    # apply_calendar 在函式內延遲 `from db import execute`，patch db 模組有效
    monkeypatch.setattr(db_module, 'execute', _flaky)
    text = (f'term 2099-01-01 2099-06-30 {TERM_LABEL}甲\n'
            f'term 2099-07-01 2099-12-31 {TERM_LABEL}乙\n'
            f'holiday {HOLIDAY_A} 測試假日\n'
            f'makeup 2099-10-10 fri 測試補課\n')
    r = admin_client.post('/admin/calendar/import', data={'calendar_text': text})
    assert r.status_code == 302
    assert not q('SELECT 1 FROM terms WHERE label LIKE ?', (f'{TERM_LABEL}%',))
    assert not q('SELECT 1 FROM holidays WHERE day=?', (HOLIDAY_A,))
    assert not q('SELECT 1 FROM makeup_days WHERE day=?', ('2099-10-10',))
    assert not q("SELECT 1 FROM audit_logs WHERE action='import_calendar' "
                 "AND detail LIKE ?", (f'{TERM_LABEL}%',))


def test_holidays_import_rolls_back_on_partial_failure(admin_client, monkeypatch):
    real_execute = app_module.execute
    calls = {'n': 0}

    def _flaky(sql, args=()):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('模擬第二筆寫入中斷')
        return real_execute(sql, args)

    monkeypatch.setattr(app_module, 'execute', _flaky)
    r = admin_client.post('/admin/holidays/import',
                          data={'lines': f'{HOLIDAY_A}\n{HOLIDAY_B},年末'})
    assert r.status_code == 302
    for day in (HOLIDAY_A, HOLIDAY_B):
        assert not q('SELECT 1 FROM holidays WHERE day=?', (day,)), \
            f'{day} 不該留在資料庫（交易應回滾）'


def test_holidays_import_still_skips_bad_lines(admin_client):
    """格式錯誤的行仍是「略過」，不是整批失敗——行為不可被交易化改壞。"""
    r = admin_client.post('/admin/holidays/import',
                          data={'lines': f'{HOLIDAY_A},年末\n不是日期\n# 註解'})
    assert r.status_code == 302
    assert q('SELECT 1 FROM holidays WHERE day=?', (HOLIDAY_A,))
    execute('DELETE FROM holidays WHERE day=?', (HOLIDAY_A,))


# --------------------------------------------------------------------------
# 3. Email 驗證：一次性 token 守衛
# --------------------------------------------------------------------------
def test_verify_email_guard_failure_changes_nothing(make_student, monkeypatch):
    """守衛回 0（併發下已被他人兌換）時，不可改 users 也不能寫稽核。"""
    uname, _ = make_student('v25_verify_stu')
    uid = _uid(uname)
    raw = app_module.issue_token(uid, 'verify')
    assert raw

    before_user = q('SELECT email_verified FROM users WHERE id=?', (uid,), one=True)
    before_logs = q("SELECT COUNT(*) c FROM audit_logs WHERE action='verify_email'",
                    one=True)['c']
    monkeypatch.setattr(app_module, 'execute_guard', lambda *a, **kw: 0)

    c = app.test_client()
    r = c.get(f'/verify/{raw}', follow_redirects=True)
    page = r.get_data(as_text=True)
    assert '已失效' in page
    assert q('SELECT email_verified FROM users WHERE id=?', (uid,), one=True) \
        == before_user
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='verify_email'",
             one=True)['c'] == before_logs


def test_verify_email_still_works_once(make_student):
    uname, _ = make_student('v25_verify_stu2')
    uid = _uid(uname)
    raw = app_module.issue_token(uid, 'verify')
    c = app.test_client()
    r = c.get(f'/verify/{raw}', follow_redirects=True)
    assert 'Email 驗證完成' in r.get_data(as_text=True)
    assert q('SELECT email_verified FROM users WHERE id=?', (uid,),
             one=True)['email_verified'] == 1
    # 重放被擋
    r2 = app.test_client().get(f'/verify/{raw}', follow_redirects=True)
    assert '無效或已逾期' in r2.get_data(as_text=True)


# --------------------------------------------------------------------------
# 4. 額度：NaN / Infinity 不得寫入
# --------------------------------------------------------------------------
@pytest.fixture()
def term_and_type():
    """自建專用學期與假別，收尾清掉，避免干擾其他測試的學期假設。"""
    tid = execute('INSERT INTO terms (label, start_date, end_date, is_current) '
                  "VALUES (?,?,?,0)",
                  (TERM_LABEL, '2099-01-01', '2099-12-31'))
    lt = execute('INSERT INTO leave_types (code, name, requires_attachment, '
                 "needs_faculty) VALUES (?,?,0,0)",
                 ('v25_test', 'v25 測試假別'))
    yield tid, lt
    execute('DELETE FROM leave_quotas WHERE term_id=?', (tid,))
    execute('DELETE FROM terms WHERE id=?', (tid,))
    execute('DELETE FROM leave_types WHERE id=?', (lt,))


@pytest.mark.parametrize('bad', ['nan', 'NaN', 'inf', '-inf', '1e400'])
def test_set_quota_rejects_non_finite(admin_client, term_and_type, bad):
    tid, lt = term_and_type
    r = admin_client.post('/admin/quotas/set', data={
        'term_id': str(tid), 'leave_type_id': str(lt), 'max_days': bad})
    assert r.status_code == 302
    assert not q('SELECT 1 FROM leave_quotas WHERE term_id=? AND leave_type_id=?',
                 (tid, lt)), f'{bad} 不該寫入額度'


def test_set_quota_accepts_normal_value(admin_client, term_and_type):
    tid, lt = term_and_type
    r = admin_client.post('/admin/quotas/set', data={
        'term_id': str(tid), 'leave_type_id': str(lt), 'max_days': '3.5'})
    assert r.status_code == 302
    row = q('SELECT max_days FROM leave_quotas WHERE term_id=? AND leave_type_id=?',
            (tid, lt), one=True)
    assert row and math.isfinite(row['max_days']) and row['max_days'] == 3.5


# --------------------------------------------------------------------------
# 5. D3：單張詳情頁逐筆 PII 稽核
# --------------------------------------------------------------------------
@pytest.fixture()
def own_request(make_student):
    """自建學生＋直接寫入一張假單，避開表單流程的日期／額度限制。"""
    uname, pw = make_student('v25_detail_stu')
    uid = _uid(uname)
    today = date.today().isoformat()
    rid = execute('INSERT INTO leave_requests (student_id, leave_type_id, '
                  'start_date, end_date, start_period, end_period, days, reason, '
                  'contact_phone, status, current_level, required_level) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (uid, 1, today, today, 'AM', 'PM', 1.0, 'v25 測試事由',
                   '13800009999', 'pending', 1, 1))
    yield rid, uid, pw
    # audit_logs.request_id 有外鍵：先刪稽核，再刪假單
    execute('DELETE FROM audit_logs WHERE request_id=?', (rid,))
    execute('DELETE FROM approvals WHERE request_id=?', (rid,))
    execute('DELETE FROM leave_requests WHERE id=?', (rid,))


def test_detail_logs_view_pii_for_non_owner(own_request):
    rid, _, _ = own_request
    c = app.test_client()
    _login(c, 'T001')                      # 導師：非當事人但有範圍檢視權
    advisor = _uid('T001')
    before = _count_view_pii(advisor, rid)
    r = c.get(f'/request/{rid}')
    assert r.status_code == 200
    assert _count_view_pii(advisor, rid) == before + 1


def test_detail_no_view_pii_for_owner(own_request):
    rid, uid, pw = own_request
    c = app.test_client()
    _login(c, 'v25_detail_stu', pw)
    before = _count_view_pii(uid, rid)
    r = c.get(f'/request/{rid}')
    assert r.status_code == 200
    assert _count_view_pii(uid, rid) == before == 0, \
        '學生看自己的單不算跨範圍 PII 讀取'


def test_detail_audit_does_not_copy_pii(own_request):
    """稽核明細不可再抄一次學號／事由等個資（v1.13.0 紀律）。"""
    rid, _, _ = own_request
    c = app.test_client()
    _login(c, 'admin')
    c.get(f'/request/{rid}')
    rows = q("SELECT detail FROM audit_logs WHERE request_id=? AND action='view_pii'",
             (rid,))
    assert rows
    for row in rows:
        assert 'v25_detail_stu' not in row['detail']
        assert 'v25 測試事由' not in row['detail']
        assert '13800009999' not in row['detail']


def test_admin_logs_page_shows_detail_pii_records(own_request):
    rid, _, _ = own_request
    c = app.test_client()
    _login(c, 'admin')
    c.get(f'/request/{rid}')
    r = c.get('/admin/logs?action=view_pii')
    assert r.status_code == 200
    assert AUDIT_ACTION_LABEL.get('view_pii') in r.get_data(as_text=True)


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------
def test_version_is_at_least_1_18_0():
    parts = tuple(int(x) for x in APP_VERSION.split('.'))
    assert parts >= (1, 18, 0)
