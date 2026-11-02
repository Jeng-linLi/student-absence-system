# -*- coding: utf-8 -*-
"""v1.4.0 測試：委託審批（B4）、管理員改派導師、學期與額度（C2）、
校曆假日（C3）、催辦升級 cron（B5）、撤回院系級假單的迴歸保護。

沿用 conftest 的共用臨時資料庫與種子資料；自建專用帳號（v14_*）避免
與其他測試檔搶種子帳號。本檔在字母序上最後執行，其中建立的 terms /
leave_quotas 不會影響先前測試（conftest 不跑 seed_calendar，學期表
原本為空）。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, is_school_off, execute  # noqa: E402
from utils import APP_VERSION  # noqa: E402

TODAY = datetime.date.today()
PW = 'V14test@2026'
DEMO = 'Demo@2026'
STU = 'v14_stu'           # 導師 T001 的學生（同院系）
STU2 = 'v14_stu2'         # 導師 T001 的學生（跨院系審批測試）
STU3 = 'v14_stu3'         # 改派導師測試
FAC2 = 'v14_fac2'         # 院系教師（數據科學學域 → 代理人工智能學域）
DEPT_AI = '人工智能學域'
DEPT_DS = '數據科學學域'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def admin_client():
    c = app.test_client()
    assert c.post('/login', data={'username': 'admin', 'password': DEMO}).status_code == 302
    return c


def _ensure_user(admin, username, name, role, dept, advisor_id=None):
    """冪等地建立測試帳號（已存在則沿用）。"""
    if q('SELECT id FROM users WHERE username=?', (username,), one=True):
        return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']
    data = {'username': username, 'password': PW, 'name': name, 'role': role,
            'department': dept, 'email': f'{username}@example.edu'}
    if advisor_id is not None:
        data['advisor_id'] = str(advisor_id)
    r = admin.post('/admin/users/add', data=data)
    assert r.status_code == 200, f'建立 {username} 失敗'
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _uid(username):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _login(username, password=PW):
    c = app.test_client()
    assert c.post('/login', data={'username': username, 'password': password}).status_code == 302
    return c


def _submit(client, day_offset, reason, days=1, type_id='1'):
    d = TODAY + datetime.timedelta(days=day_offset)
    r = client.post('/new', data={'leave_type_id': type_id,
                                  'start_date': d.isoformat(),
                                  'end_date': (d + datetime.timedelta(days=days - 1)).isoformat(),
                                  'start_period': 'AM', 'end_period': 'PM',
                                  'reason': reason, 'contact_phone': '13800000001'})
    assert r.status_code == 302, f'提交失敗（{reason}）：{r.status_code}'
    return int(r.headers['Location'].rsplit('/', 1)[-1])


def _add_delegation(admin, delegator, delegate, level, start=None, end=None):
    start = start or (TODAY - datetime.timedelta(days=1)).isoformat()
    end = end or (TODAY + datetime.timedelta(days=1)).isoformat()
    return admin.post('/admin/delegations/add', data={
        'delegator_id': str(_uid(delegator)), 'delegate_id': str(_uid(delegate)),
        'level': str(level), 'start_date': start, 'end_date': end, 'note': 'v14 測試'})


# --------------------------------------------------------------------------
# 委託審批（B4 · v1.4.0）
# --------------------------------------------------------------------------
def test_delegation_validation(admin_client):
    """同一人、日期顛倒、角色不符——都不能建立委託。"""
    before = q('SELECT COUNT(*) c FROM delegations', one=True)['c']
    # 同一人
    _add_delegation(admin_client, 'T001', 'T001', 1)
    # 結束日早於開始日
    _add_delegation(admin_client, 'T001', 'T002', 1,
                    start=TODAY.isoformat(),
                    end=(TODAY - datetime.timedelta(days=1)).isoformat())
    # 層級 1 的委託人必須是導師（F001 是院系教師）
    _add_delegation(admin_client, 'F001', 'T002', 1)
    after = q('SELECT COUNT(*) c FROM delegations', one=True)['c']
    assert before == after, '非法委託不應寫入資料庫'


def test_delegation_level1_grants_todo_and_approve(admin_client):
    """導師 T001 委託 T002 後：T002 看得到單、審得動；刪除委託後權限收回。"""
    _ensure_user(admin_client, STU, 'v14 測試生', 'student', DEPT_AI, _uid('T001'))
    stu = _login(STU)
    rid = _submit(stu, 10, 'v14 委託待審單')

    # 委託生效前：T002 看不到 T001 學生的單（待辦表以學生姓名呈現）
    t2 = _login('T002', DEMO)
    assert 'v14 測試生' not in t2.get('/approvals').get_data(as_text=True)

    r = _add_delegation(admin_client, 'T001', 'T002', 1)
    assert r.status_code == 302
    # 代理人收到委託通知
    assert q('SELECT 1 FROM notifications WHERE user_id=? AND message LIKE ?',
             (_uid('T002'), '%委託給你%'), one=True)

    # 委託生效後：待辦看得到、審得動
    html = t2.get('/approvals').get_data(as_text=True)
    assert 'v14 測試生' in html, '代理人待辦應出現委託範圍內的假單'
    r = t2.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '代理核准'},
                follow_redirects=True)
    assert r.status_code == 200
    row = q('SELECT status FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['status'] == 'approved'
    appr = q('SELECT approver_id FROM approvals WHERE request_id=? AND level=1', (rid,), one=True)
    assert appr['approver_id'] == _uid('T002'), '審批人應記錄為代理人'

    # 刪除委託後權限立即收回
    did = q('SELECT id FROM delegations WHERE delegator_id=? AND delegate_id=?',
            (_uid('T001'), _uid('T002')), one=True)['id']
    rid2 = _submit(stu, 20, 'v14 委託收回後的單')
    assert admin_client.post(f'/admin/delegations/{did}/delete').status_code == 302
    assert 'v14 測試生' not in t2.get('/approvals').get_data(as_text=True)
    t2.post(f'/request/{rid2}/decide', data={'action': 'approve', 'comment': '越權'},
            follow_redirects=True)
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid2,),
             one=True)['status'] == 'pending', '委託刪除後代理人不得再審'


def test_delegation_level2_cross_department(admin_client):
    """院系層級委託：代理人不在該院系，靠委託取得代審權。"""
    _ensure_user(admin_client, FAC2, 'v14 院辦代理', 'faculty', DEPT_DS)
    _ensure_user(admin_client, STU2, 'v14 測試生二', 'student', DEPT_AI, _uid('T001'))
    stu = _login(STU2)
    rid = _submit(stu, 30, 'v14 院系委託單', days=5)   # 5 天 → 需院系複核

    t1 = _login('T001', DEMO)
    t1.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '轉院系'},
            follow_redirects=True)
    assert q('SELECT current_level FROM leave_requests WHERE id=?', (rid,),
             one=True)['current_level'] == 2

    fac2 = _login(FAC2)
    # 委託前：跨院系院辦看不到、也審不了
    assert 'v14 測試生二' not in fac2.get('/approvals').get_data(as_text=True)
    fac2.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '越權'},
              follow_redirects=True)
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'pending'

    _add_delegation(admin_client, 'F001', FAC2, 2)
    assert 'v14 測試生二' in fac2.get('/approvals').get_data(as_text=True)
    fac2.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '代理院系核准'},
              follow_redirects=True)
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'approved'


# --------------------------------------------------------------------------
# 管理員改派導師（B4 · v1.4.0）
# --------------------------------------------------------------------------
def test_admin_reassign_advisor(admin_client):
    _ensure_user(admin_client, STU3, 'v14 測試生三', 'student', DEPT_AI, _uid('T001'))
    stu = _login(STU3)
    rid = _submit(stu, 40, 'v14 改派前提交')

    r = admin_client.post(f'/admin/request/{rid}/reassign', data={'advisor_id': str(_uid('T002'))},
                          follow_redirects=True)
    assert r.status_code == 200
    assert q('SELECT advisor_id FROM users WHERE username=?', (STU3,),
             one=True)['advisor_id'] == _uid('T002')
    # 稽核日誌與雙方通知
    assert q("SELECT 1 FROM audit_logs WHERE request_id=? AND action='reassign'",
             (rid,), one=True)
    assert q('SELECT 1 FROM notifications WHERE user_id=? AND request_id=? AND message LIKE ?',
             (_uid('T002'), rid, '%改派給你%'), one=True), '新導師應收到改派通知'
    assert q('SELECT 1 FROM notifications WHERE user_id=? AND request_id=? AND message LIKE ?',
             (q('SELECT id FROM users WHERE username=?', (STU3,), one=True)['id'], rid,
              '%導師歸屬%'), one=True), '學生應收到導師異動通知'
    # 舊導師 T001 的待辦不再出現該單
    t1 = _login('T001', DEMO)
    assert 'v14 改派前提交' not in t1.get('/approvals').get_data(as_text=True)


# --------------------------------------------------------------------------
# 學期與額度（C2 + B3 · v1.4.0）
# --------------------------------------------------------------------------
def test_terms_current_flag_exclusive(admin_client):
    admin_client.post('/admin/terms/add', data={
        'label': 'v14 秋季', 'start_date': (TODAY - datetime.timedelta(days=30)).isoformat(),
        'end_date': (TODAY + datetime.timedelta(days=30)).isoformat()})
    admin_client.post('/admin/terms/add', data={
        'label': 'v14 春季', 'start_date': (TODAY + datetime.timedelta(days=31)).isoformat(),
        'end_date': (TODAY + datetime.timedelta(days=120)).isoformat(),
        'is_current': '1'})
    # 重複標籤被擋
    admin_client.post('/admin/terms/add', data={
        'label': 'v14 秋季', 'start_date': TODAY.isoformat(),
        'end_date': TODAY.isoformat()})
    assert q('SELECT COUNT(*) c FROM terms WHERE label LIKE "v14%"', one=True)['c'] == 2
    assert q('SELECT COUNT(*) c FROM terms WHERE is_current=1', one=True)['c'] == 1

    tid_autumn = q("SELECT id FROM terms WHERE label='v14 秋季'", one=True)['id']
    assert admin_client.post(f'/admin/terms/{tid_autumn}/current').status_code == 302
    rows = q('SELECT label, is_current FROM terms WHERE label LIKE "v14%"')
    flags = {r['label']: r['is_current'] for r in rows}
    assert flags['v14 秋季'] == 1 and flags['v14 春季'] == 0, 'is_current 應互斥'


def test_quota_blocks_excess_submission(admin_client):
    """額度涵蓋待審＋已核准：連續提交多張繞不過上限。

    用專用學生 v14_stuq，避免先前測試留在「v14 秋季」學期內的
    type 1 假單污染用量計數。
    """
    term = q("SELECT id FROM terms WHERE label='v14 秋季'", one=True)
    assert term, '前置：test_terms_current_flag_exclusive 應先建立 v14 秋季'
    # 事假（type 1）額度 2 天；日期取在學期區間內（today±30）
    admin_client.post('/admin/quotas/set', data={
        'term_id': str(term['id']), 'leave_type_id': '1', 'max_days': '2'})

    _ensure_user(admin_client, 'v14_stuq', 'v14 額度生', 'student', DEPT_AI, _uid('T001'))
    stu = _login('v14_stuq')
    rid = _submit(stu, 12, 'v14 額度內的單')          # 1 天 → OK（此時已用 0）
    # 再提 2 天：1 + 2 > 2 → 擋
    d = TODAY + datetime.timedelta(days=14)
    r = stu.post('/new', data={'leave_type_id': '1',
                               'start_date': d.isoformat(),
                               'end_date': (d + datetime.timedelta(days=1)).isoformat(),
                               'start_period': 'AM', 'end_period': 'PM',
                               'reason': 'v14 超額的單', 'contact_phone': '13800000001'},
                 follow_redirects=True)
    html = r.get_data(as_text=True)
    assert '額度' in html and '超出上限' in html, '超額提交應被擋下並說明原因'
    assert not q("SELECT 1 FROM leave_requests WHERE reason='v14 超額的單'", one=True)

    # 學生儀表板的額度板顯示用量（1 / 2）
    from app import student_quota_board
    board = student_quota_board(q('SELECT id FROM users WHERE username=?',
                                  ('v14_stuq',), one=True)['id'])[1]
    row = [b for b in board if b['type_name'] == '事假'][0]
    assert row['max_days'] == 2 and row['used'] == 1

    # 清空 max_days → 取消限制後可再提交
    admin_client.post('/admin/quotas/set', data={
        'term_id': str(term['id']), 'leave_type_id': '1', 'max_days': ''})
    _submit(stu, 16, 'v14 取消額度後的單')

    # 還原：撤銷第一張，避免影響後續測試的日期重疊判斷
    stu.post(f'/request/{rid}/withdraw')


# --------------------------------------------------------------------------
# 校曆假日（C3 · v1.4.0）
# --------------------------------------------------------------------------
def test_holidays_add_import_delete(admin_client):
    # 找一個未來的平日（避開週末，週末本來就是非上課日）
    day = TODAY + datetime.timedelta(days=7)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    assert not is_school_off(day), '前置：平日不應是假日'

    r = admin_client.post('/admin/holidays/add', data={'day': day.isoformat(), 'label': '校慶'},
                          follow_redirects=True)
    assert r.status_code == 200
    assert is_school_off(day), '加入校曆後該日應視為非上課日'

    # 批次匯入：兩行有效（含 tab 分隔標籤）＋一行格式錯誤
    d2 = day + datetime.timedelta(days=1)
    while d2.weekday() >= 5:
        d2 += datetime.timedelta(days=1)
    d3 = d2 + datetime.timedelta(days=1)
    while d3.weekday() >= 5:
        d3 += datetime.timedelta(days=1)
    r = admin_client.post('/admin/holidays/import', data={
        'lines': f'not-a-date\n{d2.isoformat()},運動會\n{d3.isoformat()}\t校慶翌日'},
        follow_redirects=True)
    html = r.get_data(as_text=True)
    assert '已匯入 2' in html and '1 行格式不合法' in html
    assert is_school_off(d2) and is_school_off(d3)
    assert not q("SELECT 1 FROM holidays WHERE label='not-a-date'", one=True)

    hid = q('SELECT id FROM holidays WHERE day=?', (day.isoformat(),), one=True)['id']
    assert admin_client.post(f'/admin/holidays/{hid}/delete').status_code == 302
    assert not is_school_off(day), '刪除後該日應恢復為上課日'


# --------------------------------------------------------------------------
# 催辦升級 cron（B5 · v1.4.0）
# --------------------------------------------------------------------------
def _backdate(rid, days):
    old = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    execute('UPDATE leave_requests SET created_at=? WHERE id=?', (old, rid))


def test_cron_escalate(admin_client, monkeypatch):
    _ensure_user(admin_client, STU, 'v14 測試生', 'student', DEPT_AI, _uid('T001'))
    stu = _login(STU)
    rid = _submit(stu, 90, 'v14 催辦目標單', type_id='5')   # 其他（無附件、單級）
    _backdate(rid, 5)

    # 非管理員（且未設 token）→ 403
    assert stu.get('/admin/cron/escalate').status_code == 403

    t1_before = q('SELECT COUNT(*) c FROM notifications WHERE user_id=?',
                  (_uid('T001'),), one=True)['c']
    r = admin_client.post('/admin/cron/escalate')
    data = r.get_json()
    assert r.status_code == 200 and data['status'] == 'ok'
    assert data['reminded'] >= 1 and data['escalated'] >= 1
    assert q("SELECT 1 FROM audit_logs WHERE request_id=? AND action='escalate'",
             (rid,), one=True), '催辦應留下稽核憑據'
    t1_after = q('SELECT COUNT(*) c FROM notifications WHERE user_id=?',
                 (_uid('T001'),), one=True)['c']
    assert t1_after > t1_before, '當前審批人應收到催辦提醒'
    # 升級：第 1 級卡單 → 院系（F001）與管理員都收到
    assert q('SELECT 1 FROM notifications WHERE user_id=? AND message LIKE ?',
             (_uid('F001'), '%升級%'), one=True)
    assert q('SELECT 1 FROM notifications WHERE message LIKE ? AND user_id IN '
             "(SELECT id FROM users WHERE role='admin')", ('%升級%',), one=True)

    # 24 小時冷卻：立即重跑 → 同一單被跳過
    r2 = admin_client.post('/admin/cron/escalate')
    assert r2.get_json()['skipped_cooldown'] >= 1

    # 設定 token 後：無 token 拒絕、正確 token 放行（管理員 session 也一律走 token）
    monkeypatch.setenv('LEAVE_CRON_TOKEN', 'v14-cron-secret')
    assert app.test_client().post('/admin/cron/escalate',
                                  headers={'X-Cron-Token': 'wrong'}).status_code == 403
    r3 = app.test_client().post('/admin/cron/escalate',
                                headers={'X-Cron-Token': 'v14-cron-secret'})
    assert r3.status_code == 200 and r3.get_json()['status'] == 'ok'
    monkeypatch.delenv('LEAVE_CRON_TOKEN')


# --------------------------------------------------------------------------
# 撤回院系級假單：current_level==2 的迴歸保護（v1.4.0 修）
# --------------------------------------------------------------------------
def test_withdraw_at_level2_no_error(admin_client):
    """導師已核准轉院系（current_level=2）後學生撤回——不得 500，
    且院系審批人要收到通知（待辦數字才對得上）。"""
    _ensure_user(admin_client, STU2, 'v14 測試生二', 'student', DEPT_AI, _uid('T001'))
    stu = _login(STU2)
    rid = _submit(stu, 100, 'v14 撤回院系級單', days=5)
    t1 = _login('T001', DEMO)
    t1.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '轉院系'},
            follow_redirects=True)
    assert q('SELECT current_level FROM leave_requests WHERE id=?', (rid,),
             one=True)['current_level'] == 2

    r = stu.post(f'/request/{rid}/withdraw')
    assert r.status_code == 302, '撤回不應拋出例外'
    assert q('SELECT status FROM leave_requests WHERE id=?', (rid,),
             one=True)['status'] == 'withdrawn'
    assert q('SELECT 1 FROM notifications WHERE user_id=? AND request_id=? AND message LIKE ?',
             (_uid('F001'), rid, '%撤回%'), one=True), '院系審批人應收到撤回通知'


def test_healthz_reports_version():
    """/healthz 的版本號要與 utils.APP_VERSION 一致。

    版本號每次發版都會動，所以這裡只驗「一致」＋「不小於 v1.4.0」，
    不再把數字寫死在測試裡（否則每次發版都得回來改測試）。
    """
    r = app.test_client().get('/healthz')
    assert r.status_code == 200
    assert r.get_json()['version'] == APP_VERSION
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 4, 0)
