# -*- coding: utf-8 -*-
"""v1.11.0 測試：附件掃毒通道（GAP-ANALYSIS D2）。

守住幾件事：
  1. 未配置 LEAVE_AV_CMD 時通道關閉——上傳照常，行為與 v1.10.0 完全一致；
  2. 配置後（任意退出碼可編程的假掃毒命令）：
     - 退出碼 1（感染）→ 拒收：不落 uploads/、不寫假單、留 av_block 稽核；
     - 退出碼 0（乾淨）→ 正常收件、檔案在 uploads/；
     - 退出碼 3（掃描器故障）→ 依 LEAVE_AV_FAIL_POLICY：reject 拒收、
       allow 放行但留稽核；
  3. 稽核標籤註冊：後台 /admin/logs 動作篩選自動收錄 av_block；
  4. flask av-scan 重掃存量：感染檔隔離到 uploads/quarantine/、
     對應假單 attachment 欄清空、留稽核。

掃毒命令用測試自建的假腳本（依參數退出），不依賴真實 ClamAV。
"""
import datetime
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, UPLOAD_DIR  # noqa: E402
from db import UPLOAD_DIR as UPLOAD_DIR_DB  # noqa: E402,F401  (同一目錄)
from utils import APP_VERSION  # noqa: E402

TODAY = datetime.date.today()
PW = 'V1test@2026'
STU = 'v21_stu'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def student():
    """自建一名學生（導師 T001），回傳 username。"""
    t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
    admin = app.test_client()
    admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
    if not q('SELECT id FROM users WHERE username=?', (STU,), one=True):
        r = admin.post('/admin/users/add', data={
            'username': STU, 'password': PW, 'name': 'v21 測試生',
            'role': 'student', 'department': '人工智能學域',
            'email': f'{STU}@example.edu', 'advisor_id': str(t1)})
        assert r.status_code == 200, '建立 v21_stu 失敗'
    return STU


@pytest.fixture(autouse=True)
def _av_env(monkeypatch):
    """每個測試預設清掉掃毒配置，需要時再自行 setenv。"""
    monkeypatch.delenv('LEAVE_AV_CMD', raising=False)
    monkeypatch.delenv('LEAVE_AV_FAIL_POLICY', raising=False)


@pytest.fixture
def fake_av(tmp_path, monkeypatch):
    """生成假掃毒命令：`<python> <script> <code> {file}`，code 由呼叫方指定。"""
    script = tmp_path / 'fake_av.py'
    script.write_text('import sys\nsys.exit(int(sys.argv[1]))\n', encoding='utf-8')
    exe = sys.executable.replace('\\', '/')

    def _make(code):
        cmd = f'"{exe}" "{script.as_posix()}" {code} {{file}}'
        monkeypatch.setenv('LEAVE_AV_CMD', cmd)
        return cmd

    return _make


def _login(client, username, password=PW):
    return client.post('/login', data={'username': username, 'password': password})


def _post_new(client, day_offset, reason, with_file=True):
    d = TODAY + datetime.timedelta(days=day_offset)
    data = {'leave_type_id': '1', 'start_date': d.isoformat(),
            'end_date': d.isoformat(), 'start_period': 'AM', 'end_period': 'AM',
            'reason': reason, 'contact_phone': '13800000021'}
    if with_file:
        data['attachment'] = (io.BytesIO(b'%PDF-1.4\n' + b'x' * 4096), 'proof.pdf')
    return client.post('/new', data=data, content_type='multipart/form-data')


def _av_blocks_like(needle):
    return q("SELECT COUNT(*) c FROM audit_logs WHERE action='av_block' "
             "AND detail LIKE ?", (f'%{needle}%',), one=True)['c']


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------
def test_version_is_1_11_0():
    assert APP_VERSION == '1.11.0'


# --------------------------------------------------------------------------
# 通道關閉：未配置 LEAVE_AV_CMD 時行為不變
# --------------------------------------------------------------------------
def test_av_disabled_by_default(student):
    c = app.test_client()
    _login(c, student)
    r = _post_new(c, 300, 'v21 通道關閉')
    assert r.status_code == 302
    rid = int(r.headers['Location'].rsplit('/', 1)[-1])
    row = q('SELECT attachment FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['attachment']
    assert os.path.exists(os.path.join(UPLOAD_DIR, row['attachment']))
    assert _av_blocks_like('v21 通道關閉') == 0


# --------------------------------------------------------------------------
# 感染檔：拒收、不落 uploads/、不寫假單、留 av_block 稽核
# --------------------------------------------------------------------------
def test_infected_upload_blocked(student, fake_av):
    fake_av(1)
    c = app.test_client()
    _login(c, student)
    before = set(os.listdir(UPLOAD_DIR)) if os.path.isdir(UPLOAD_DIR) else set()
    r = _post_new(c, 301, 'v21 感染附件')
    assert r.status_code == 200                       # 重渲染表單，不是 500
    assert '未通過安全掃描' in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) c FROM leave_requests WHERE reason='v21 感染附件'",
             one=True)['c'] == 0
    assert _av_blocks_like('proof.pdf') >= 1
    # uploads/ 沒有新增檔案（先臨時檔掃毒，感染自始不進附件區）
    after = set(os.listdir(UPLOAD_DIR)) if os.path.isdir(UPLOAD_DIR) else set()
    assert after - before == set()


# --------------------------------------------------------------------------
# 乾淨檔：正常收件
# --------------------------------------------------------------------------
def test_clean_upload_passes(student, fake_av):
    fake_av(0)
    c = app.test_client()
    _login(c, student)
    r = _post_new(c, 302, 'v21 乾淨附件')
    assert r.status_code == 302
    rid = int(r.headers['Location'].rsplit('/', 1)[-1])
    row = q('SELECT attachment FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['attachment'].endswith('.pdf')
    assert os.path.exists(os.path.join(UPLOAD_DIR, row['attachment']))


# --------------------------------------------------------------------------
# 掃描器故障：reject（預設）拒收；allow 放行但留稽核
# --------------------------------------------------------------------------
def test_scanner_error_policy_reject(student, fake_av, monkeypatch):
    fake_av(3)                                        # 非 0/1 → 掃描器故障
    c = app.test_client()
    _login(c, student)
    r = _post_new(c, 303, 'v21 故障拒收')
    assert r.status_code == 200
    assert '掃描未完成' in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) c FROM leave_requests WHERE reason='v21 故障拒收'",
             one=True)['c'] == 0
    assert _av_blocks_like('proof.pdf') >= 1


def test_scanner_error_policy_allow(student, fake_av, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'AV_FAIL_POLICY', 'allow')
    fake_av(3)
    c = app.test_client()
    _login(c, student)
    r = _post_new(c, 304, 'v21 故障放行')
    assert r.status_code == 302                       # fail-open：照常收件
    rid = int(r.headers['Location'].rsplit('/', 1)[-1])
    row = q('SELECT attachment FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['attachment']
    assert _av_blocks_like('策略放行') >= 1


# --------------------------------------------------------------------------
# 稽核標籤 + CLI 存量重掃
# --------------------------------------------------------------------------
def test_av_block_label_registered():
    from app import AUDIT_ACTION_LABEL
    assert AUDIT_ACTION_LABEL.get('av_block') == '附件掃毒攔截'


def test_cli_av_scan_quarantines_infected(fake_av, student, capsys):
    from app import av_scan_command
    fake_av(1)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fn = 'v21_infected_existing.pdf'
    with open(os.path.join(UPLOAD_DIR, fn), 'wb') as f:
        f.write(b'%PDF-1.4\n' + b'y' * 1024)
    ltype = q('SELECT id FROM leave_types LIMIT 1', one=True)
    stu_id = q('SELECT id FROM users WHERE username=?', (STU,), one=True)['id']
    rid = execute(
        'INSERT INTO leave_requests (student_id, leave_type_id, start_date, '
        'end_date, days, reason, attachment, status) VALUES (?,?,?,?,?,?,?,?)',
        (stu_id, ltype['id'], '2026-03-02', '2026-03-03', 2.0,
         'v21 存量感染', fn, 'approved'))
    runner = app.test_cli_runner()
    result = runner.invoke(av_scan_command, [])
    assert result.exit_code == 0, result.output
    assert not os.path.exists(os.path.join(UPLOAD_DIR, fn))
    assert os.path.exists(os.path.join(UPLOAD_DIR, 'quarantine', fn))
    row = q('SELECT attachment FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['attachment'] == ''
    assert _av_blocks_like(fn) >= 1
