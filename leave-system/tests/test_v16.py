# -*- coding: utf-8 -*-
"""v1.6.0 測試：批次審批（GAP-ANALYSIS B6）。

主軸：批次與單張審批共用 _apply_decision()，因此核准轉下一級、通知對象、
稽核動作文案、並發守衛必須完全一致；而批次 id 是可竄改的表單欄位，
越權、已結案或已被他人處理的單只能被略過，不能被審批。

沿用 conftest 的共用臨時資料庫與種子資料；自建專用帳號（v16_*）避免
與其他測試檔搶種子帳號。本檔字母序在 test_v15 之後，最後執行。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from werkzeug.datastructures import MultiDict  # noqa: E402

from app import app, q, execute  # noqa: E402
from utils import APP_VERSION, MAX_BULK_DECIDE  # noqa: E402

TODAY = datetime.date.today()
PW = 'V16test@2026'
DEMO = 'Demo@2026'
STU = 'v16_stu'            # 導師 T001 的學生
STU_OTHER = 'v16_other'    # 導師 T002 的學生（越權測試）
DEPT_AI = '人工智能學域'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def admin_client():
    c = app.test_client()
    assert c.post('/login', data={'username': 'admin', 'password': DEMO}).status_code == 302
    return c


def _uid(username):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _ensure_user(admin, username, name, role, dept, advisor_id=None):
    if q('SELECT id FROM users WHERE username=?', (username,), one=True):
        return _uid(username)
    data = {'username': username, 'password': PW, 'name': name, 'role': role,
            'department': dept, 'email': f'{username}@example.edu'}
    if advisor_id is not None:
        data['advisor_id'] = str(advisor_id)
    assert admin.post('/admin/users/add', data=data).status_code == 200
    return _uid(username)


def _login(username, password=PW):
    c = app.test_client()
    assert c.post('/login', data={'username': username, 'password': password}).status_code == 302
    return c


def _next_offset(days=1, counter=[700]):
    """每段取一個不重疊的日期區間，避免「與既有假單重疊」擋下提交。

    偏移從 700 起（約兩年後），避開 v14/v15 建立的學期、額度與校曆假日。
    """
    start = counter[0]
    counter[0] += days + 2
    return start


def _submit(client, offset, reason, days=1, type_id='1'):
    d = TODAY + datetime.timedelta(days=offset)
    r = client.post('/new', data={'leave_type_id': type_id,
                                  'start_date': d.isoformat(),
                                  'end_date': (d + datetime.timedelta(days=days - 1)).isoformat(),
                                  'start_period': 'AM', 'end_period': 'PM',
                                  'reason': reason, 'contact_phone': '13800000001'})
    assert r.status_code == 302, f'提交失敗（{reason}）：{r.status_code}'
    return int(r.headers['Location'].rsplit('/', 1)[-1])


def _bulk(client, ids, action='approve', comment='批次核准'):
    """ids 是多值欄位；werkzeug 的 data 只接受 mapping，故用 MultiDict。"""
    data = MultiDict()
    for rid in ids:
        data.add('ids', str(rid))
    data['action'] = action
    data['comment'] = comment
    return client.post('/approvals/bulk', data=data, follow_redirects=True)


def _cleanup(ids):
    marks = ','.join('?' * len(ids))
    for tbl in ('approvals', 'audit_logs', 'notifications'):
        execute(f'DELETE FROM {tbl} WHERE request_id IN ({marks})', ids)
    execute(f'DELETE FROM leave_requests WHERE id IN ({marks})', ids)


@pytest.fixture()
def setup_students(admin_client):
    """建立 v16 專用學生，並清掉其他測試可能留下的委託（避免干擾越權斷言）。"""
    execute('DELETE FROM delegations')
    _ensure_user(admin_client, STU, 'v16 學生', 'student', DEPT_AI, _uid('T001'))
    _ensure_user(admin_client, STU_OTHER, 'v16 他生', 'student', DEPT_AI, _uid('T002'))
    return STU


# --------------------------------------------------------------------------
# 版本號
# --------------------------------------------------------------------------
def test_app_version_bumped():
    """v1.6.0：版本號已提升（healthz 與頁腳都從 APP_VERSION 帶入）。"""
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 6, 0)


# --------------------------------------------------------------------------
# 批次核准／駁回
# --------------------------------------------------------------------------
def test_bulk_approve_multiple(setup_students):
    """一次核准三張：全部生效，狀態與單張審批一致。"""
    stu = _login(STU)
    rids = [_submit(stu, _next_offset(), f'v16 批次{i}') for i in range(3)]
    t = _login('T001', DEMO)
    html = _bulk(t, rids).get_data(as_text=True)
    assert '已核准 3 張假單' in html
    for rid in rids:
        assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
                 one=True)['status'] == 'approved'
    _cleanup(rids)


def test_bulk_reject_multiple(setup_students):
    """一次駁回多張，理由確實寫進 approvals.comment。"""
    stu = _login(STU)
    rids = [_submit(stu, _next_offset(), f'v16 駁回{i}') for i in range(2)]
    t = _login('T001', DEMO)
    html = _bulk(t, rids, action='reject', comment='集體公假不成立').get_data(as_text=True)
    assert '已駁回 2 張假單' in html
    for rid in rids:
        assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
                 one=True)['status'] == 'rejected'
    rows = q('SELECT comment FROM approvals WHERE request_id=? AND level=1', (rids[0],))
    assert any(r['comment'] == '集體公假不成立' for r in rows), '理由未寫入審批列'
    _cleanup(rids)


# --------------------------------------------------------------------------
# 與單張審批共用邏輯
# --------------------------------------------------------------------------
def test_bulk_level1_transitions_to_level2(setup_students):
    """六天假單批次核准後轉院系複核，並通知院辦（與單張同一份邏輯）。"""
    stu = _login(STU)
    rids = [_submit(stu, _next_offset(6), f'v16 轉級{i}', days=6) for i in range(2)]
    t = _login('T001', DEMO)
    html = _bulk(t, rids).get_data(as_text=True)
    assert '已核准 2 張假單' in html
    for rid in rids:
        row = q('SELECT status, current_level FROM leave_requests WHERE id=?', (rid,), one=True)
        assert row['status'] == 'pending' and row['current_level'] == 2
    notes = q("SELECT message FROM notifications WHERE request_id IN (?,?) "
              "AND message LIKE '%待院系複核%'", (rids[0], rids[1]))
    assert len(notes) >= 2, '批次核准未沿用 notify_approvers 的院系通知'
    _cleanup(rids)


def test_bulk_uses_shared_audit_wording(setup_students):
    """稽核動作文案與單張審批一致（共用 _apply_decision 的迴歸保護）。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 文案')
    t = _login('T001', DEMO)
    _bulk(t, [rid])
    rows = q("SELECT detail FROM audit_logs WHERE request_id=? AND action='approve'", (rid,))
    assert any('第1級核准' in r['detail'] for r in rows), '批次審批未沿用單張的稽核文案'
    _cleanup([rid])


# --------------------------------------------------------------------------
# 邊界與越權防護
# --------------------------------------------------------------------------
def test_bulk_skips_unauthorized(setup_students):
    """摻進別的導師的假單：只被略過，不得越權核准。"""
    stu = _login(STU)
    mine = _submit(stu, _next_offset(), 'v16 我的')
    other = _login(STU_OTHER)
    theirs = _submit(other, _next_offset(), 'v16 他人的')
    t = _login('T001', DEMO)
    html = _bulk(t, [mine, theirs]).get_data(as_text=True)
    assert '已核准 1 張假單' in html and '略過 1 張' in html
    assert q('SELECT status FROM leave_requests WHERE id=?', (mine,),
             one=True)['status'] == 'approved'
    assert q('SELECT status FROM leave_requests WHERE id=?', (theirs,),
             one=True)['status'] == 'pending', '越權假單不得被核准'
    _cleanup([mine, theirs])


def test_bulk_skips_non_pending(setup_students):
    """已結案的單摻進批次也只被略過，不會被重新處理。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 結案')
    t = _login('T001', DEMO)
    _bulk(t, [rid])
    again = _bulk(t, [rid]).get_data(as_text=True)
    assert '沒有可處理的假單' in again
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'approved'
    _cleanup([rid])


def test_bulk_concurrent_guard(setup_students):
    """該層審批列已被他人處理：計入略過，不覆寫他人結果。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 並發')
    execute("UPDATE approvals SET action='approve' WHERE request_id=? AND level=1", (rid,))
    t = _login('T001', DEMO)
    html = _bulk(t, [rid]).get_data(as_text=True)
    assert '沒有可處理的假單' in html
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'pending', '不得覆寫他人的審批結果'
    _cleanup([rid])


def test_bulk_empty_and_limit(setup_students):
    """沒勾選、超過單次上限都要被擋下。"""
    t = _login('T001', DEMO)
    empty = t.post('/approvals/bulk', data={'action': 'approve'},
                   follow_redirects=True).get_data(as_text=True)
    assert '請先勾選' in empty
    too_many = list(range(1, MAX_BULK_DECIDE + 2))
    html = _bulk(t, too_many).get_data(as_text=True)
    assert f'單次最多處理 {MAX_BULK_DECIDE} 張' in html


def test_bulk_reject_requires_reason(setup_students):
    """批次駁回與單張一樣必須填理由。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 駁回理由')
    t = _login('T001', DEMO)
    html = _bulk(t, [rid], action='reject', comment='').get_data(as_text=True)
    assert '駁回必須填寫理由' in html
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'pending'
    _cleanup([rid])


def test_bulk_requires_approver_role(setup_students):
    """學生不能進批次審批端點（舊版 /approvals 曾漏過的角色下限）。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 角色')
    r = stu.post('/approvals/bulk', data={'ids': str(rid), 'action': 'approve'})
    assert r.status_code == 302
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'pending'
    _cleanup([rid])


def test_bulk_todo_page_has_checkboxes(setup_students):
    """待審視圖出現批次表單；「全部假單」視圖不給勾選（避免誤勾已結案單）。"""
    stu = _login(STU)
    rid = _submit(stu, _next_offset(), 'v16 頁面')
    t = _login('T001', DEMO)
    todo = t.get('/approvals').get_data(as_text=True)
    assert 'name="ids"' in todo and '/approvals/bulk' in todo
    all_view = t.get('/approvals?scope=all').get_data(as_text=True)
    assert 'name="ids"' not in all_view
    _cleanup([rid])
