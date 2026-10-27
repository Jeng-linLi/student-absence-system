# -*- coding: utf-8 -*-
"""v1.2.0 測試：交易內不寄信（郵件佇列）、approvals 狀態篩選、
帳號管理分頁、CSV 串流匯出、requires_attachment 孤兒檔案預檢、healthz 版本號。

沿用 conftest 的共用臨時資料庫與種子資料；自建專用學生 v12_stu（掛 T001），
登入與提交都走它，避免與其他測試檔搶帳號。
"""
import datetime
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app as app_module  # noqa: E402
from app import app, q  # noqa: E402
from utils import APP_VERSION  # noqa: E402

TODAY = datetime.date.today()
PW = 'V12test@2026'
STU = 'v12_stu'            # 導師 T001 的學生


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def student():
    """自建一名掛 T001 的學生（已存在則沿用）。"""
    t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
    admin = app.test_client()
    admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
    if not q('SELECT id FROM users WHERE username=?', (STU,), one=True):
        r = admin.post('/admin/users/add', data={
            'username': STU, 'password': PW, 'name': 'v12 測試生',
            'role': 'student', 'department': '人工智能學域',
            'email': f'{STU}@example.edu', 'advisor_id': str(t1)})
        assert r.status_code == 200, '建立 v12_stu 失敗'
    return q('SELECT id FROM users WHERE username=?', (STU,), one=True)['id']


def _login(client, username, password=PW):
    return client.post('/login', data={'username': username, 'password': password})


def _submit(client, day_offset, reason='v12 測試', days=1, type_id='1'):
    """以已登入學生身份提交一張假單，回傳 rid。"""
    d = TODAY + datetime.timedelta(days=day_offset)
    r = client.post('/new', data={'leave_type_id': type_id,
                                  'start_date': d.isoformat(),
                                  'end_date': (d + datetime.timedelta(days=days - 1)).isoformat(),
                                  'start_period': 'AM', 'end_period': 'PM',
                                  'reason': reason, 'contact_phone': '13800000001'},
                    content_type='multipart/form-data')
    assert r.status_code == 302, f'提交失敗：{r.status_code}'
    return int(r.headers['Location'].rsplit('/', 1)[-1])


def _enable_smtp(monkeypatch):
    monkeypatch.setenv('LEAVE_SMTP_HOST', 'smtp.test.invalid')
    monkeypatch.setenv('LEAVE_SMTP_USER', 'u')
    monkeypatch.setenv('LEAVE_SMTP_FROM', 'f@test.invalid')


# --------------------------------------------------------------------------
# 交易內不寄信（v1.2.0）
# --------------------------------------------------------------------------
def test_approval_email_sent_after_commit(student, monkeypatch):
    """核准流程的通知郵件確實寄出，但寄出時交易已結束——不該在
    SQLite 寫入鎖持有期間做 SMTP 連線。"""
    sent = []

    def fake_send(to_addr, subject, body):
        from flask import g as _g
        assert getattr(_g, '_tx_depth', 0) == 0, '郵件不應在交易進行中寄出'
        sent.append(to_addr)

    monkeypatch.setattr(app_module, 'send_email', fake_send)
    _enable_smtp(monkeypatch)

    stu = app.test_client()
    _login(stu, STU)
    rid = _submit(stu, 500)
    t1 = app.test_client()
    _login(t1, 'T001', 'Demo@2026')
    r = t1.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': 'ok'})
    assert r.status_code == 302
    assert sent == [f'{STU}@example.edu'], f'核准後應收到一封學生通知郵件：{sent}'


def test_atomic_commit_flushes_queued_email(monkeypatch):
    """atomic() 成功提交後，佇列中的郵件才寄出。"""
    sent = []
    monkeypatch.setattr(app_module, 'send_email', lambda *a: sent.append(a))
    _enable_smtp(monkeypatch)
    uid = q("SELECT id FROM users WHERE username='2024001'", one=True)['id']

    with app.app_context():
        with app_module.atomic():
            app_module.notify(uid, None, '交易內的通知')
            assert sent == [], '交易還沒提交，郵件不該寄出'
        assert len(sent) == 1
        assert sent[0][0] == '2024001@example.edu'


def test_atomic_rollback_discards_queued_email(monkeypatch):
    """交易回滾時，佇列中累積的郵件整批作廢——站內信的寫入隨交易
    消失，郵件不該單獨寄到使用者手上。"""
    sent = []
    monkeypatch.setattr(app_module, 'send_email', lambda *a: sent.append(a))
    _enable_smtp(monkeypatch)
    uid = q("SELECT id FROM users WHERE username='2024001'", one=True)['id']
    before = q('SELECT COUNT(*) c FROM notifications', one=True)['c']

    with app.app_context():
        with pytest.raises(RuntimeError):
            with app_module.atomic():
                app_module.notify(uid, None, '不該收到的通知')
                raise RuntimeError('boom')
        assert sent == [], '交易回滾後不應寄出任何郵件'
    assert q('SELECT COUNT(*) c FROM notifications', one=True)['c'] == before, \
        '站內通知也應隨交易回滾'


def test_bare_notify_outside_tx_sends_immediately(monkeypatch):
    """交易外（CLI／seed 路徑）的 notify 立即寄出——沒有 after_request
    會來收割佇列。"""
    sent = []
    monkeypatch.setattr(app_module, 'send_email', lambda *a: sent.append(a))
    _enable_smtp(monkeypatch)
    uid = q("SELECT id FROM users WHERE username='2024001'", one=True)['id']

    with app.app_context():
        app_module.notify(uid, None, '交易外立即寄')
        assert len(sent) == 1


# --------------------------------------------------------------------------
# /approvals?scope=all 狀態篩選（v1.2.0）
# --------------------------------------------------------------------------
def _rid_in(html, rid):
    return re.search(r'>%d</td>' % rid, html) is not None


def test_approvals_scope_all_status_filter(student):
    stu = app.test_client()
    _login(stu, STU)
    r_pending = _submit(stu, 600, reason='v12 待審')
    r_approved = _submit(stu, 601, reason='v12 核准')
    r_rejected = _submit(stu, 602, reason='v12 駁回')
    r_withdrawn = _submit(stu, 603, reason='v12 撤回')

    t1 = app.test_client()
    _login(t1, 'T001', 'Demo@2026')
    assert t1.post(f'/request/{r_approved}/decide',
                   data={'action': 'approve', 'comment': 'ok'}).status_code == 302
    assert t1.post(f'/request/{r_rejected}/decide',
                   data={'action': 'reject', 'comment': '理由'}).status_code == 302
    assert stu.post(f'/request/{r_withdrawn}/withdraw').status_code == 302

    admin = app.test_client()
    _login(admin, 'admin', 'Demo@2026')

    html = admin.get('/approvals?scope=all&status=approved').get_data(as_text=True)
    assert _rid_in(html, r_approved)
    assert not _rid_in(html, r_pending) and not _rid_in(html, r_rejected) \
        and not _rid_in(html, r_withdrawn), '已核准篩選不該混入其他狀態'

    html = admin.get('/approvals?scope=all&status=rejected').get_data(as_text=True)
    assert _rid_in(html, r_rejected) and not _rid_in(html, r_approved)

    html = admin.get('/approvals?scope=all&status=withdrawn').get_data(as_text=True)
    assert _rid_in(html, r_withdrawn) and not _rid_in(html, r_approved)

    html = admin.get('/approvals?scope=all&status=pending').get_data(as_text=True)
    assert _rid_in(html, r_pending) and not _rid_in(html, r_approved)

    # 白名單外的狀態值一律視為不篩選（四張單都該出現）
    html = admin.get('/approvals?scope=all&status=bogus').get_data(as_text=True)
    for rid in (r_pending, r_approved, r_rejected, r_withdrawn):
        assert _rid_in(html, rid), '非法 status 不該造成過濾'


# --------------------------------------------------------------------------
# 帳號管理分頁（v1.2.0）
# --------------------------------------------------------------------------
def test_admin_users_pagination():
    admin = app.test_client()
    _login(admin, 'admin', 'Demo@2026')
    for i in range(1, 26):
        r = admin.post('/admin/users/add', data={
            'username': f'v12_u{i:02d}', 'password': 'Page@2026x',
            'name': f'分頁測試 {i}', 'role': 'student', 'department': '人工智能學域'})
        assert r.status_code == 200, f'建立 v12_u{i:02d} 失敗'

    total = q('SELECT COUNT(*) c FROM users', one=True)['c']
    assert max((total + 19) // 20, 1) >= 2, '帳號數應超過一頁'

    def page_of(username):
        """依列表排序（role, username）推算該帳號落在第幾頁——
        不假設其他測試檔建立了哪些帳號。"""
        before = q('SELECT COUNT(*) c FROM users WHERE (role, username) < '
                   '(SELECT role, username FROM users WHERE username=?)',
                   (username,), one=True)['c']
        return before // 20 + 1

    p1, p2 = page_of('v12_u01'), page_of('v12_u25')
    assert p1 != p2, 'v12_u01 與 v12_u25 相隔 25 個帳號，必落在不同頁'

    html1 = admin.get(f'/admin/users?page={p1}').get_data(as_text=True)
    assert 'v12_u01' in html1 and 'v12_u25' not in html1
    assert f'第 {p1} /' in html1, '應顯示分頁資訊'

    html2 = admin.get(f'/admin/users?page={p2}').get_data(as_text=True)
    assert 'v12_u25' in html2 and 'v12_u01' not in html2, '新帳號應收納在後面的頁'

    # 角色篩選與分頁可組合：role=student 翻到有 v12_u25 的頁為止，
    # 每一頁都不該出現管理員／導師帳號
    for p in range(1, 6):
        s = admin.get(f'/admin/users?role=student&page={p}').get_data(as_text=True)
        assert not re.search(r'mono">admin<', s), '角色篩選後不該出現其他角色帳號'
        assert not re.search(r'mono">T00', s), '角色篩選後不該出現導師帳號'
        if 'v12_u25' in s:
            break
    else:
        raise AssertionError('role=student 前 5 頁內應能找到 v12_u25')


# --------------------------------------------------------------------------
# CSV 串流匯出（v1.2.0）
# --------------------------------------------------------------------------
def test_export_csv_streaming_content(student):
    stu = app.test_client()
    _login(stu, STU)
    _submit(stu, 700, reason='=1+1 公式注入')       # csv_safe 前綴守衛
    _submit(stu, 701, reason='v12 匯出第二筆')

    admin = app.test_client()
    _login(admin, 'admin', 'Demo@2026')
    r = admin.get('/export')
    assert r.status_code == 200
    assert 'text/csv' in r.headers['Content-Type']

    text = r.get_data(as_text=True).encode('utf-8').decode('utf-8-sig')
    lines = [ln for ln in text.splitlines() if ln.strip()]
    total = q('SELECT COUNT(*) c FROM leave_requests', one=True)['c']
    assert lines[0].startswith('假單號'), '表頭應在（BOM 之後）第一列'
    assert len(lines) == total + 1, f'串流匯出應包含全部 {total} 筆假單'
    assert "'=1+1 公式注入" in text, '公式注入防護在串流模式下應依然生效'


# --------------------------------------------------------------------------
# requires_attachment 預檢（v1.2.0）：不在 uploads/ 留孤兒檔案
# --------------------------------------------------------------------------
def test_requires_attachment_leaves_no_orphan_file(student):
    uploads = Path(app_module.UPLOAD_DIR)
    before = set(uploads.glob('*')) if uploads.exists() else set()

    stu = app.test_client()
    _login(stu, STU)
    d = TODAY + datetime.timedelta(days=800)
    r = stu.post('/new', data={'leave_type_id': '2',       # 病假：必須附證明
                               'start_date': d.isoformat(),
                               'end_date': d.isoformat(),
                               'start_period': 'AM', 'end_period': 'PM',
                               'reason': 'v12 沒附證明', 'contact_phone': '13800000001'},
                 content_type='multipart/form-data')
    html = r.get_data(as_text=True)
    assert '必須上傳證明文件' in html, '缺證明文件的病假應被擋下'
    assert q("SELECT id FROM leave_requests WHERE reason='v12 沒附證明'") == [], \
        '被擋下的假單不應寫入資料庫'

    after = set(uploads.glob('*')) if uploads.exists() else set()
    assert after == before, '被擋下的上傳不該在 uploads/ 留下孤兒檔案'


# --------------------------------------------------------------------------
# healthz 版本號（v1.2.0）
# --------------------------------------------------------------------------
def test_healthz_reports_version():
    r = app.test_client().get('/healthz')
    assert r.status_code == 200
    data = r.get_json()
    assert data['status'] == 'ok' and data['version'] == APP_VERSION
