# -*- coding: utf-8 -*-
"""v1.13.0 測試：列表搜尋（E4）、審批通知補齊與既有正確性缺口。

守住幾件事：
  1. `q`／`date_from`／`date_to` 的正規化、`LIKE` 萬用字元跳脫、逆序日期對調；
  2. `/my`、`/approvals`（todo 與 all）、`/stats`、`/export` 都吃同一組篩選，
     且搜尋只會收窄 `role_scope()`，不會變成跨範圍列舉；
  3. `?term=<不存在 id>` 回落到目前學期，不再靜默顯示全部歷史；
  4. 第一級核准會通知學生、核准意見會進學生通知；
  5. 同檔名被多張假單引用時，附件授權逐筆檢查；
  6. 附件销毁交易失敗時，已搬走的檔案會搬回，DB 欄位不懸空。
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from app import app, q, execute  # noqa: E402
from utils import (APP_VERSION, MAX_SEARCH_Q_LEN, like_keyword,  # noqa: E402
                   parse_list_filters, search_conditions)

PW = 'V23test@2026'
STU_A, STU_B = 'v23_stu_a', 'v23_stu_b'
NAME_A, NAME_B = '搜尋甲生', '搜尋乙生'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture(scope='module', autouse=True)
def _term():
    """保證有一個涵蓋今天的學期。

    `seed()` 刻意不建學期（那是 `seed_calendar()` 的職責，見 app.py），
    單獨跑本檔時 terms 可能是空的。這裡缺什麼補什麼，收尾只刪自己補的，
    不影響其他測試檔對學期資料的假設。
    """
    with app.app_context():
        created = None
        if not q('SELECT 1 FROM terms LIMIT 1'):
            today = date.today()
            created = execute(
                'INSERT INTO terms (label, start_date, end_date, is_current) '
                'VALUES (?,?,?,1)',
                ('v23 測試學期', today.replace(month=1, day=1).isoformat(),
                 today.replace(month=12, day=31).isoformat()))
        yield
        if created:
            execute('DELETE FROM terms WHERE id=?', (created,))


@pytest.fixture(scope='module')
def data():
    """建立專用學生與假單，避免與前面測試檔改過密碼的示範帳號打架。"""
    with app.app_context():
        t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
        t2 = q("SELECT id FROM users WHERE username='T002'", one=True)['id']
        pw_hash = generate_password_hash(PW)
        for uname, name, dept, cls, advisor in (
                (STU_A, NAME_A, '人工智能學域', 'V23 甲班', t1),
                (STU_B, NAME_B, '數據科學學域', 'V23 乙班', t2)):
            if not q('SELECT id FROM users WHERE username=?', (uname,), one=True):
                execute('INSERT INTO users '
                        '(username, password_hash, name, role, department, class_name, '
                        ' email, advisor_id) VALUES (?,?,?,?,?,?,?,?)',
                        (uname, pw_hash, name, 'student', dept, cls, '', advisor))
        uid_a = q('SELECT id FROM users WHERE username=?', (STU_A,), one=True)['id']
        uid_b = q('SELECT id FROM users WHERE username=?', (STU_B,), one=True)['id']
        result = {
            'uid_a': uid_a, 'uid_b': uid_b,
            'rid_nov': _insert_request(uid_a, '關鍵字甲', '2026-11-10', '2026-11-11'),
            'rid_dec': _insert_request(uid_a, '另一事由', '2026-12-01', '2026-12-02'),
            'rid_other': _insert_request(uid_b, '關鍵字乙', '2026-11-12', '2026-11-12'),
        }
        yield result


def _insert_request(student_id, reason, start, end, *, status='pending',
                    current_level=1, required_level=1, attachment='',
                    updated_at=None):
    type_id = q('SELECT id FROM leave_types WHERE active=1 ORDER BY id LIMIT 1',
                one=True)['id']
    sql = ('INSERT INTO leave_requests '
           '(student_id, leave_type_id, start_date, end_date, days, reason, '
           ' attachment, status, current_level, required_level, updated_at) '
           'VALUES (?,?,?,?,?,?,?,?,?,?,COALESCE(?, datetime(\'now\',\'localtime\')))')
    return execute(sql, (student_id, type_id, start, end, 1.0, reason,
                         attachment, status, current_level, required_level,
                         updated_at))


def _login(username, password=PW):
    c = app.test_client()
    r = c.post('/login', data={'username': username, 'password': password})
    assert r.status_code == 302, f'{username} 登入失敗：{r.status_code}'
    return c


def _rendered_ids(html):
    """只抓列表第一欄的假單編號，避免日期／分頁網址裡的數字造成誤判。"""
    return set(re.findall(r'<td class="mono">(\d+)</td>', html))


# --------------------------------------------------------------------------
# 純函式：輸入正規化與 LIKE 安全
# --------------------------------------------------------------------------
def test_version_is_at_least_1_13_0():
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 13, 0)


def test_parse_list_filters_normalizes_and_swaps_dates():
    f = parse_list_filters({'q': '  100%_!  ', 'date_from': '2026-12-01',
                            'date_to': '2026-11-01'})
    assert f == {'q': '100%_!', 'date_from': '2026-11-01',
                 'date_to': '2026-12-01'}
    f2 = parse_list_filters({'q': 'x' * 200, 'date_from': '不是日期',
                             'date_to': ''})
    assert f2['q'] == 'x' * MAX_SEARCH_Q_LEN
    assert f2['date_from'] == '' and f2['date_to'] == ''


def test_like_keyword_escapes_wildcards():
    assert like_keyword('100%_!') == '%100!%!_!!%'
    conds, args = search_conditions(
        {'q': 'a%', 'date_from': '2026-11-01', 'date_to': '2026-11-30'},
        ('s.name', 'r.reason'))
    assert len(conds) == 3
    assert "ESCAPE '!'" in conds[0]
    assert args[:2] == [like_keyword('a%'), like_keyword('a%')]
    assert args[2:] == ['2026-11-01', '2026-11-30']


# --------------------------------------------------------------------------
# /my：學生自己的列表
# --------------------------------------------------------------------------
def test_my_requests_search_and_date_filters(data):
    c = _login(STU_A)
    html = c.get('/my', query_string={'q': '關鍵字甲'}).get_data(as_text=True)
    ids = _rendered_ids(html)
    assert str(data['rid_nov']) in ids
    assert str(data['rid_dec']) not in ids
    assert 'name="q"' in html and 'name="date_from"' in html

    html2 = c.get('/my', query_string={'date_from': '2026-12-01',
                                       'date_to': '2026-12-31'}).get_data(as_text=True)
    ids2 = _rendered_ids(html2)
    assert str(data['rid_dec']) in ids2
    assert str(data['rid_nov']) not in ids2

    # `%` 必須當字面值，不能退化成「撈出全部」
    html3 = c.get('/my', query_string={'q': '%'}).get_data(as_text=True)
    assert _rendered_ids(html3) == set()


# --------------------------------------------------------------------------
# /approvals：todo 與 all，範圍隔離不被搜尋放寬
# --------------------------------------------------------------------------
def test_approvals_search_and_scope_isolation(data):
    t1 = _login('T001', 'Demo@2026')
    todo = t1.get('/approvals', query_string={'q': '關鍵字甲'}).get_data(as_text=True)
    todo_ids = _rendered_ids(todo)
    assert str(data['rid_nov']) in todo_ids
    assert str(data['rid_other']) not in todo_ids
    assert 'name="q"' in todo and 'name="date_to"' in todo

    # T001 只管自己的學生；即使明確搜尋別人的姓名也不得撈出資料
    out_of_scope = t1.get('/approvals', query_string={
        'scope': 'all', 'q': NAME_B}).get_data(as_text=True)
    assert str(data['rid_other']) not in _rendered_ids(out_of_scope)

    admin = _login('admin', 'Demo@2026')
    all_html = admin.get('/approvals', query_string={
        'scope': 'all', 'q': NAME_B}).get_data(as_text=True)
    all_ids = _rendered_ids(all_html)
    assert str(data['rid_other']) in all_ids
    assert str(data['rid_nov']) not in all_ids


def test_count_todo_uses_same_filters_as_list(data):
    u = q("SELECT * FROM users WHERE username='T001'", one=True)
    all_count = app_module.count_todo(u)
    filtered = app_module.count_todo(u, {'q': '關鍵字甲', 'date_from': '',
                                         'date_to': ''})
    assert all_count >= 1
    assert 1 <= filtered < all_count


# --------------------------------------------------------------------------
# /stats、/export：同樣的條件與學期回落
# --------------------------------------------------------------------------
def test_resolve_term_filter_falls_back_to_current_term():
    current = app_module.current_term()
    assert current is not None
    term, no_term = app_module.resolve_term_filter('999999')
    assert not no_term and term['id'] == current['id']
    term0, no_term0 = app_module.resolve_term_filter('0')
    assert term0 is None and no_term0 is True


def test_stats_search_and_invalid_term_shows_current_term(data):
    admin = _login('admin', 'Demo@2026')
    html = admin.get('/stats', query_string={'q': NAME_A}).get_data(as_text=True)
    assert NAME_A in html and NAME_B not in html
    assert 'name="q"' in html and 'name="date_from"' in html

    current = app_module.current_term()
    bad = admin.get('/stats', query_string={'term': '999999'}).get_data(as_text=True)
    assert 'value="0" selected' not in bad
    assert f'value="{current["id"]}" selected' in bad
    zero = admin.get('/stats', query_string={'term': '0'}).get_data(as_text=True)
    assert 'value="0" selected' in zero


def test_export_respects_keyword_date_and_term_filters(data):
    admin = _login('admin', 'Demo@2026')
    keyword = admin.get('/export', query_string={'q': '關鍵字甲'}).get_data(as_text=True)
    assert '關鍵字甲' in keyword and '關鍵字乙' not in keyword

    december = admin.get('/export', query_string={
        'q': NAME_A, 'date_from': '2026-12-01',
        'date_to': '2026-12-31'}).get_data(as_text=True)
    assert '另一事由' in december and '關鍵字甲' not in december
    audit = q("SELECT detail FROM audit_logs WHERE action='export' "
              'ORDER BY id DESC LIMIT 1', one=True)['detail']
    assert '篩選：q,date_from,date_to' in audit

    term = app_module.current_term()
    scoped = admin.get('/export', query_string={
        'q': NAME_A, 'term': str(term['id'])}).get_data(as_text=True)
    assert '關鍵字甲' in scoped and '搜尋乙生' not in scoped


# --------------------------------------------------------------------------
# C4：核准通知不再漏掉進度與意見
# --------------------------------------------------------------------------
def test_level1_and_final_approval_notify_student_with_comment(data):
    rid = _insert_request(data['uid_a'], 'v23 兩級通知', '2026-11-20',
                          '2026-11-25', current_level=1, required_level=2)
    for level in (1, 2):
        execute("INSERT INTO approvals (request_id, level, action) "
                "VALUES (?,?,'pending')", (rid, level))

    t1 = _login('T001', 'Demo@2026')
    r1 = t1.post(f'/request/{rid}/decide',
                 data={'action': 'approve', 'comment': '准假但補作業'})
    assert r1.status_code == 302
    notes = [n['message'] for n in q(
        'SELECT message FROM notifications WHERE user_id=? AND request_id=?',
        (data['uid_a'], rid))]
    assert any('已通過第1級審批' in n and '准假但補作業' in n for n in notes)
    assert all('已核准' not in n for n in notes), '中繼進度不該長得像終態核准'

    admin = _login('admin', 'Demo@2026')
    r2 = admin.post(f'/request/{rid}/decide',
                    data={'action': 'approve', 'comment': '核備完成'})
    assert r2.status_code == 302
    notes = [n['message'] for n in q(
        'SELECT message FROM notifications WHERE user_id=? AND request_id=?',
        (data['uid_a'], rid))]
    assert any('已核准' in n and '核備完成' in n for n in notes)


# --------------------------------------------------------------------------
# 附件授權：同檔名多筆時逐筆檢查
# --------------------------------------------------------------------------
def test_attachment_authorization_checks_all_matching_requests(data, monkeypatch):
    monkeypatch.setattr(app_module, 'send_from_directory',
                        lambda *a, **kw: app.response_class('ok'))
    dup = 'v23_dup.pdf'
    only = 'v23_only.pdf'
    _insert_request(data['uid_a'], 'v23 同檔甲', '2026-11-10',
                    '2026-11-10', attachment=dup)
    _insert_request(data['uid_b'], 'v23 同檔乙', '2026-11-10',
                    '2026-11-10', attachment=dup)
    _insert_request(data['uid_a'], 'v23 專屬檔', '2026-11-10',
                    '2026-11-10', attachment=only)

    t2 = _login('T002', 'Demo@2026')
    assert t2.get(f'/uploads/{dup}').status_code == 200, \
        '第二筆所屬導師也應能下載同一份檔'
    stu_b = _login(STU_B)
    assert stu_b.get(f'/uploads/{dup}').status_code == 200
    assert stu_b.get(f'/uploads/{only}').status_code == 302, \
        '沒有任何可見假單引用該檔名時仍須拒絕'


# --------------------------------------------------------------------------
# D1：檔案搬移與 DB 交易的一致性
# --------------------------------------------------------------------------
def test_purge_restores_file_when_db_write_fails(data, monkeypatch, tmp_path):
    backup_dir = tmp_path / 'purged'
    monkeypatch.setenv('LEAVE_ATTACHMENT_BACKUP_DIR', str(backup_dir))
    os.makedirs(app_module.UPLOAD_DIR, exist_ok=True)
    fn = 'v23_purge_failed.pdf'
    src = os.path.join(app_module.UPLOAD_DIR, fn)
    with open(src, 'wb') as f:
        f.write(b'%PDF-1.4\n')
    rid = _insert_request(data['uid_a'], 'v23 清理失敗', '2026-11-10',
                          '2026-11-10', status='approved', attachment=fn,
                          updated_at='2000-01-01 00:00:00')

    original_execute = app_module.execute

    def _fail_attachment_update(sql, args=()):
        if sql.startswith('UPDATE leave_requests SET attachment'):
            raise RuntimeError('DB write failed')
        return original_execute(sql, args)

    monkeypatch.setattr(app_module, 'execute', _fail_attachment_update)
    try:
        # 留存期取預設 365 天：本筆 updated_at 是 2000 年必定命中，
        # 其他測試的資料都在近期，不會被掃進來。
        result = app_module.purge_expired_attachments(days=365, dry_run=False)
        states = [d[2] for d in result['details'] if d[0] == rid]
        assert states == ['update_failed']
        assert os.path.exists(src), '交易失敗後檔案要搬回 uploads/'
        assert not os.path.exists(os.path.join(str(backup_dir), fn)), \
            '搬回後備份區不該留下副本'
        row = q('SELECT attachment FROM leave_requests WHERE id=?',
                (rid,), one=True)
        assert row['attachment'] == fn, '交易失敗後 DB 不該留下空白附件欄'
    finally:
        if os.path.exists(src):
            os.remove(src)
