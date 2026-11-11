# -*- coding: utf-8 -*-
"""v1.20.0 測試：共享附件生命週期與 Email 異動安全。

守住三件事：
  1. 留存清理按檔名處理全部引用；有效假單仍引用時保留實體檔；
  2. 存量掃毒隔離共享感染檔時解除全部引用，失敗則復原檔案與資料庫；
  3. Email 真正異動後重設驗證狀態、作廢舊 verify/reset token，
     新地址收到新驗證連結；只改大小寫不觸發上述副作用。
"""
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from app import app, q, execute, UPLOAD_DIR  # noqa: E402

OLD_TS = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d %H:%M:%S')
RECENT_TS = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    path = tmp_path / 'purged'
    monkeypatch.setenv('LEAVE_ATTACHMENT_BACKUP_DIR', str(path))
    return path


@pytest.fixture
def smtp_mail(monkeypatch):
    monkeypatch.setenv('LEAVE_SMTP_HOST', 'smtp.test')
    monkeypatch.setenv('LEAVE_SMTP_USER', 'noreply@test')
    monkeypatch.setenv('LEAVE_SMTP_FROM', 'noreply@test')
    sent = []
    monkeypatch.setattr(app_module, 'send_email',
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def _student_id():
    return q("SELECT id FROM users WHERE role='student' LIMIT 1", one=True)['id']


def _insert_request(attachment, status='approved', updated_at=OLD_TS, reason='v29'):
    leave_type = q('SELECT id FROM leave_types LIMIT 1', one=True)['id']
    return execute(
        'INSERT INTO leave_requests '
        '(student_id, leave_type_id, start_date, end_date, days, reason, '
        ' attachment, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (_student_id(), leave_type, '2026-01-05', '2026-01-06', 2.0,
         reason, attachment, status, updated_at))


def _write_upload(filename, content=b'%PDF-1.4\nv29'):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, 'wb') as f:
        f.write(content)
    return path


def _states(result, request_ids):
    return {rid: state for rid, _fn, state in result['details'] if rid in request_ids}


def _attachments(request_ids):
    marks = ','.join('?' for _ in request_ids)
    return {r['id']: r['attachment'] for r in q(
        f'SELECT id, attachment FROM leave_requests WHERE id IN ({marks})',
        tuple(request_ids))}


def _make_user(username, email, verified=1):
    return execute(
        'INSERT INTO users (username, password_hash, name, role, department, '
        'email, email_verified) VALUES (?,?,?,?,?,?,?)',
        (username, generate_password_hash('V29test@2026'), username,
         'student', '人工智能學域', email, verified))


def _edit_user(uid, email):
    client = app.test_client()
    response = client.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
    assert response.status_code == 302
    response = client.post(f'/admin/users/{uid}/edit', data={
        'name': 'v29 測試生', 'department': '人工智能學域',
        'class_name': 'v29', 'email': email, 'advisor_id': ''})
    assert response.status_code == 302
    return client


# --------------------------------------------------------------------------
# 附件留存：共享檔名必須整組考慮
# --------------------------------------------------------------------------
def test_purge_detaches_only_expired_reference_and_preserves_shared_file(backup_dir):
    fn = 'v29_shared_retained.pdf'
    src = _write_upload(fn)
    expired = _insert_request(fn)
    retained = _insert_request(fn, status='pending', updated_at=OLD_TS,
                               reason='v29 有效共享引用')

    result = app_module.purge_expired_attachments(days=365)
    assert _states(result, {expired, retained}) == {expired: 'detached_shared'}
    assert _attachments([expired, retained]) == {expired: '', retained: fn}
    assert os.path.exists(src), '有效假單仍引用時不可搬走實體檔'
    assert not (backup_dir / fn).exists()


def test_purge_moves_shared_file_once_when_all_references_expire(backup_dir):
    fn = 'v29_shared_all_expired.pdf'
    src = _write_upload(fn, b'shared-content')
    first = _insert_request(fn, reason='v29 全到期甲')
    second = _insert_request(fn, status='withdrawn', reason='v29 全到期乙')

    result = app_module.purge_expired_attachments(days=365)
    assert _states(result, {first, second}) == {first: 'purged', second: 'purged'}
    assert _attachments([first, second]) == {first: '', second: ''}
    assert not os.path.exists(src)
    assert (backup_dir / fn).read_bytes() == b'shared-content'


def test_purge_dry_run_distinguishes_shared_detach_from_file_removal(backup_dir):
    shared = 'v29_dry_shared.pdf'
    removable = 'v29_dry_removable.pdf'
    shared_src = _write_upload(shared)
    removable_src = _write_upload(removable)
    expired = _insert_request(shared, reason='v29 dry shared')
    retained = _insert_request(shared, status='pending', reason='v29 dry retained')
    removable_id = _insert_request(removable, reason='v29 dry removable')

    result = app_module.purge_expired_attachments(days=365, dry_run=True)
    assert _states(result, {expired, retained, removable_id}) == {
        expired: 'would_detach_shared', removable_id: 'would_purge'}
    assert _attachments([expired, retained, removable_id]) == {
        expired: shared, retained: shared, removable_id: removable}
    assert os.path.exists(shared_src) and os.path.exists(removable_src)
    assert not backup_dir.exists() or not list(backup_dir.iterdir())


def test_purge_shared_file_is_restored_when_group_update_fails(backup_dir, monkeypatch):
    fn = 'v29_shared_update_failed.pdf'
    src = _write_upload(fn)
    first = _insert_request(fn, reason='v29 失敗復原甲')
    second = _insert_request(fn, reason='v29 失敗復原乙')
    original_execute = app_module.execute

    def fail_target_group(sql, args=()):
        if (sql.startswith('UPDATE leave_requests SET attachment')
                and args and args[-1] in (first, second)):
            raise RuntimeError('simulated DB failure')
        return original_execute(sql, args)

    monkeypatch.setattr(app_module, 'execute', fail_target_group)
    result = app_module.purge_expired_attachments(days=365)
    assert _states(result, {first, second}) == {
        first: 'update_failed', second: 'update_failed'}
    assert _attachments([first, second]) == {first: fn, second: fn}
    assert os.path.exists(src)
    assert not (backup_dir / fn).exists()


# --------------------------------------------------------------------------
# 存量掃毒：所有引用一起清理，失敗一起復原
# --------------------------------------------------------------------------
def _fake_av_for(monkeypatch, infected_name):
    monkeypatch.setenv('LEAVE_AV_CMD', 'fake-av {file}')

    def fake_scan(path):
        if os.path.basename(path) == infected_name:
            return 'infected', 'v29-test-signature'
        return 'clean', 'v29-clean'

    monkeypatch.setattr(app_module, 'scan_file_for_malware', fake_scan)


def test_av_scan_quarantines_all_shared_references(monkeypatch):
    fn = 'v29_shared_infected.pdf'
    src = _write_upload(fn)
    first = _insert_request(fn, reason='v29 感染共享甲')
    second = _insert_request(fn, status='pending', reason='v29 感染共享乙')
    _fake_av_for(monkeypatch, fn)

    result = app.test_cli_runner().invoke(app_module.av_scan_command, [])
    assert result.exit_code == 0, result.output
    assert _attachments([first, second]) == {first: '', second: ''}
    assert not os.path.exists(src)
    assert os.path.exists(os.path.join(UPLOAD_DIR, 'quarantine', fn))
    audits = q("SELECT request_id FROM audit_logs WHERE action='av_block' "
               'AND detail LIKE ? ORDER BY request_id', (f'%{fn}%',))
    assert [r['request_id'] for r in audits] == sorted([first, second])


def test_av_scan_restores_shared_file_when_database_update_fails(monkeypatch):
    fn = 'v29_shared_infected_restore.pdf'
    src = _write_upload(fn)
    first = _insert_request(fn, reason='v29 感染復原甲')
    second = _insert_request(fn, status='pending', reason='v29 感染復原乙')
    _fake_av_for(monkeypatch, fn)
    original_execute = app_module.execute

    def fail_target_file(sql, args=()):
        if (sql.startswith('UPDATE leave_requests SET attachment')
                and args and args[-1] == fn):
            raise RuntimeError('simulated DB failure')
        return original_execute(sql, args)

    monkeypatch.setattr(app_module, 'execute', fail_target_file)
    result = app.test_cli_runner().invoke(app_module.av_scan_command, [])
    assert result.exit_code != 0
    assert '檔案已搬回原處' in result.output
    assert _attachments([first, second]) == {first: fn, second: fn}
    assert os.path.exists(src)
    assert not os.path.exists(os.path.join(UPLOAD_DIR, 'quarantine', fn))


# --------------------------------------------------------------------------
# Email 異動：舊地址不能再兌換新帳號資料的安全連結
# --------------------------------------------------------------------------
def test_email_change_revokes_old_tokens_and_verifies_new_address(smtp_mail):
    uid = _make_user('v29_email_change', 'old-v29@example.edu', verified=1)
    old_verify = app_module.issue_token(uid, 'verify')
    old_reset = app_module.issue_token(uid, 'reset')

    client = _edit_user(uid, 'new-v29@example.edu')
    row = q('SELECT email, email_verified FROM users WHERE id=?', (uid,), one=True)
    assert row['email'] == 'new-v29@example.edu' and row['email_verified'] == 0
    for raw, purpose in ((old_verify, 'verify'), (old_reset, 'reset')):
        token = q('SELECT used_at FROM password_resets WHERE token_hash=? AND purpose=?',
                  (app_module._hash_token(raw), purpose), one=True)
        assert token['used_at'] is not None

    assert len(smtp_mail) == 1
    to, _subject, body = smtp_mail[0]
    assert to == 'new-v29@example.edu'
    new_verify = re.search(r'/verify/([A-Za-z0-9_\-]+)', body).group(1)
    assert new_verify != old_verify

    response = client.get(f'/verify/{old_verify}', follow_redirects=True)
    assert '無效或已逾期' in response.get_data(as_text=True)
    response = client.get(f'/reset/{old_reset}', follow_redirects=True)
    assert '無效或已逾期' in response.get_data(as_text=True)
    response = client.get(f'/verify/{new_verify}', follow_redirects=True)
    assert 'Email 驗證完成' in response.get_data(as_text=True)
    assert q('SELECT email_verified FROM users WHERE id=?', (uid,),
             one=True)['email_verified'] == 1


def test_email_case_only_change_preserves_verification_and_tokens(smtp_mail):
    uid = _make_user('v29_email_same', 'same-v29@example.edu', verified=1)
    raw_verify = app_module.issue_token(uid, 'verify')
    raw_reset = app_module.issue_token(uid, 'reset')

    _edit_user(uid, '  SAME-V29@EXAMPLE.EDU  ')
    row = q('SELECT email, email_verified FROM users WHERE id=?', (uid,), one=True)
    assert row['email'] == 'SAME-V29@EXAMPLE.EDU' and row['email_verified'] == 1
    for raw in (raw_verify, raw_reset):
        assert q('SELECT used_at FROM password_resets WHERE token_hash=?',
                 (app_module._hash_token(raw),), one=True)['used_at'] is None
    assert smtp_mail == []


def test_email_removal_revokes_tokens_without_sending_mail(smtp_mail):
    uid = _make_user('v29_email_remove', 'remove-v29@example.edu', verified=1)
    raw_verify = app_module.issue_token(uid, 'verify')
    raw_reset = app_module.issue_token(uid, 'reset')

    _edit_user(uid, '')
    row = q('SELECT email, email_verified FROM users WHERE id=?', (uid,), one=True)
    assert row['email'] == '' and row['email_verified'] == 0
    for raw in (raw_verify, raw_reset):
        assert q('SELECT used_at FROM password_resets WHERE token_hash=?',
                 (app_module._hash_token(raw),), one=True)['used_at'] is not None
    assert smtp_mail == []
