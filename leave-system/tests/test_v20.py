# -*- coding: utf-8 -*-
"""v1.10.0 測試：附件留存期與銷毀（GAP-ANALYSIS D1）。

守住幾件事：
  1. 終態（approved／rejected／withdrawn）且超過留存期的附件會被銷毀：
     檔案搬離 uploads/、attachment 欄清空、並寫一筆 purge_attachment 稽核；
  2. 非終態（pending）的附件即便超期也不動；
  3. 留存期邊界：剛好未超期的不銷毀，超期才銷毀；
  4. dry_run 只預覽、不動檔案不寫庫不留稽核。

沿用 conftest 的共用臨時資料庫與上傳目錄。
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, UPLOAD_DIR, purge_expired_attachments  # noqa: E402
from utils import APP_VERSION  # noqa: E402


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


def _insert_request(attachment, status, updated_at):
    """插入一筆合法的假單，回傳 request id。"""
    student = q("SELECT id FROM users WHERE role='student' LIMIT 1", one=True)
    ltype = q("SELECT id FROM leave_types LIMIT 1", one=True)
    rid = execute(
        "INSERT INTO leave_requests "
        "(student_id, leave_type_id, start_date, end_date, days, reason, "
        " attachment, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (student['id'], ltype['id'], '2026-01-05', '2026-01-06', 2.0,
         '病假', attachment, status, updated_at),
    )
    return rid


def _old_ts(years=2):
    return (datetime.now() - timedelta(days=365 * years)).strftime('%Y-%m-%d %H:%M:%S')


def _recent_ts():
    return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')


@pytest.fixture
def _backup_dir(monkeypatch, tmp_path):
    d = tmp_path / 'backup'
    d.mkdir()
    monkeypatch.setenv('LEAVE_ATTACHMENT_BACKUP_DIR', str(d))
    return str(d)


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------
def test_version_is_1_10_0():
    assert APP_VERSION == '1.10.0'


# --------------------------------------------------------------------------
# D1：附件留存期銷毀
# --------------------------------------------------------------------------
def _write_attachment(fn):
    p = os.path.join(UPLOAD_DIR, fn)
    with open(p, 'wb') as f:
        f.write(b'fake medical certificate')
    return p


def test_terminal_expired_attachment_is_purged(_backup_dir):
    fn = 'proof_purged.txt'
    _write_attachment(fn)
    rid = _insert_request(fn, 'approved', _old_ts())
    result = purge_expired_attachments(days=365, dry_run=False)
    assert result['purged'] == 1
    # 檔案已搬離活動上傳區
    assert not os.path.exists(os.path.join(UPLOAD_DIR, fn))
    # 已備份至備份區
    assert os.path.exists(os.path.join(_backup_dir, fn))
    # attachment 欄清空
    assert q('SELECT attachment FROM leave_requests WHERE id=?', (rid,),
             one=True)['attachment'] == ''
    # 寫了一筆 purge_attachment 稽核
    assert q('SELECT COUNT(*) c FROM audit_logs WHERE action=? AND request_id=?',
             ('purge_attachment', rid), one=True)['c'] == 1


def test_pending_attachment_never_purged(_backup_dir):
    fn = 'proof_pending.txt'
    _write_attachment(fn)
    rid = _insert_request(fn, 'pending', _old_ts())
    result = purge_expired_attachments(days=365, dry_run=False)
    # pending 非終態，不應被銷毀
    assert result['purged'] == 0
    assert os.path.exists(os.path.join(UPLOAD_DIR, fn))
    assert q('SELECT attachment FROM leave_requests WHERE id=?', (rid,),
             one=True)['attachment'] == fn
    # 這張單不該留下任何 purge_attachment 稽核（按單查，避免 session 共用庫干擾）
    assert q('SELECT COUNT(*) c FROM audit_logs WHERE action=? AND request_id=?',
             ('purge_attachment', rid), one=True)['c'] == 0


def test_retention_boundary(_backup_dir):
    # 未超期：昨天更新、留存 365 天 → 不銷毀
    fn_recent = 'proof_recent.txt'
    _write_attachment(fn_recent)
    rid_recent = _insert_request(fn_recent, 'approved', _recent_ts())
    result = purge_expired_attachments(days=365, dry_run=False)
    assert result['purged'] == 0
    assert os.path.exists(os.path.join(UPLOAD_DIR, fn_recent))
    # 清理這筆以免干擾其他測試
    execute('DELETE FROM leave_requests WHERE id=?', (rid_recent,))


def test_dry_run_does_not_mutate(_backup_dir):
    fn = 'proof_dryrun.txt'
    _write_attachment(fn)
    rid = _insert_request(fn, 'approved', _old_ts())
    result = purge_expired_attachments(days=365, dry_run=True)
    assert result['dry_run'] is True
    assert result['purged'] == 0
    # 檔案仍在、欄位仍在、無稽核
    assert os.path.exists(os.path.join(UPLOAD_DIR, fn))
    assert q('SELECT attachment FROM leave_requests WHERE id=?', (rid,),
             one=True)['attachment'] == fn
    # 這張單不該留下任何 purge_attachment 稽核（按單查，避免 session 共用庫干擾）
    assert q('SELECT COUNT(*) c FROM audit_logs WHERE action=? AND request_id=?',
             ('purge_attachment', rid), one=True)['c'] == 0


def test_purge_audit_label_registered():
    from app import AUDIT_ACTION_LABEL
    assert AUDIT_ACTION_LABEL.get('purge_attachment') == '銷毀逾期附件'
