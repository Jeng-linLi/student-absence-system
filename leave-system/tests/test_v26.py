# -*- coding: utf-8 -*-
"""v1.19.0：D4 通知偏好／每日摘要、巢狀交易郵件與附件補償刪除。"""
import datetime
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from app import app, execute, q  # noqa: E402
from utils import APP_VERSION  # noqa: E402

PW = 'Digest@2026'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _no_smtp(monkeypatch):
    for key in ('LEAVE_SMTP_HOST', 'LEAVE_SMTP_USER', 'LEAVE_SMTP_FROM'):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def mail(monkeypatch):
    """配置 SMTP 並捕捉郵件；回傳 True 模擬成功送達。"""
    monkeypatch.setenv('LEAVE_SMTP_HOST', 'smtp.test')
    monkeypatch.setenv('LEAVE_SMTP_USER', 'noreply@test')
    monkeypatch.setenv('LEAVE_SMTP_FROM', 'noreply@test')
    sent = []

    def fake_send(to_addr, subject, body):
        sent.append((to_addr, subject, body))
        return True

    monkeypatch.setattr(app_module, 'send_email', fake_send)
    return sent


def _user(label, mode=None):
    username = f'v26_{label}'
    uid = execute(
        'INSERT INTO users (username, password_hash, name, role, department, email) '
        'VALUES (?,?,?,?,?,?)',
        (username, generate_password_hash(PW), label, 'student', '人工智能學域',
         f'{username}@example.edu'))
    if mode:
        _set_mode(uid, mode)
    return uid, f'{username}@example.edu'


def _set_mode(uid, mode):
    execute('INSERT INTO user_prefs (user_id, email_enabled, digest_daily) VALUES (?,?,?) '
            'ON CONFLICT(user_id) DO UPDATE SET email_enabled=excluded.email_enabled, '
            'digest_daily=excluded.digest_daily',
            (uid, int(mode != 'off'), int(mode == 'daily')))


def _login(uid):
    username = q('SELECT username FROM users WHERE id=?', (uid,), one=True)['username']
    client = app.test_client()
    assert client.post('/login', data={'username': username, 'password': PW}).status_code == 302
    return client


def test_version_and_digest_schema():
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 19, 0)
    cols = {row['name'] for row in q('PRAGMA table_info(notifications)')}
    assert {'digest_pending', 'digest_sent_at', 'digest_claim', 'digest_claimed_at'} <= cols
    assert q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_prefs'", one=True)


def test_default_preference_remains_immediate(mail):
    uid, email = _user('default')
    app_module.notify(uid, None, '預設即時通知')
    assert [m[0] for m in mail] == [email]
    row = q('SELECT digest_pending FROM notifications WHERE user_id=?', (uid,), one=True)
    assert row['digest_pending'] == 0


def test_email_off_keeps_only_in_app_notification(mail):
    uid, _ = _user('off', 'off')
    app_module.notify(uid, None, '只留站內通知')
    assert mail == []
    row = q('SELECT digest_pending, message FROM notifications WHERE user_id=?', (uid,), one=True)
    assert row['digest_pending'] == 0 and row['message'] == '只留站內通知'


def test_notification_preferences_route_updates_only_own_row(mail):
    uid, _ = _user('prefs')
    client = _login(uid)
    response = client.post('/notifications/preferences', data={'email_mode': 'daily'})
    assert response.status_code == 302
    row = q('SELECT email_enabled, digest_daily FROM user_prefs WHERE user_id=?', (uid,), one=True)
    assert (row['email_enabled'], row['digest_daily']) == (1, 1)
    page = client.get('/notifications').get_data(as_text=True)
    assert '每日摘要（合併為一封）' in page and '安全郵件不受此設定影響' in page

    client.post('/notifications/preferences', data={'email_mode': 'off'})
    row = q('SELECT email_enabled, digest_daily FROM user_prefs WHERE user_id=?', (uid,), one=True)
    assert (row['email_enabled'], row['digest_daily']) == (0, 0)
    client.post('/notifications/preferences', data={'email_mode': 'bogus'})
    row = q('SELECT email_enabled, digest_daily FROM user_prefs WHERE user_id=?', (uid,), one=True)
    assert (row['email_enabled'], row['digest_daily']) == (0, 0)


def test_opt_out_cancels_pending_and_worker_enforces_preference(mail):
    uid, _ = _user('optout', 'daily')
    app_module.notify(uid, None, '退訂後不得寄出的摘要')
    client = _login(uid)
    response = client.post('/notifications/preferences', data={'email_mode': 'off'})
    assert response.status_code == 302
    row = q('SELECT digest_pending, digest_claim FROM notifications WHERE user_id=?',
            (uid,), one=True)
    assert row['digest_pending'] == 0 and row['digest_claim'] is None

    # 模擬直接改庫或舊程序留下的待送列：worker 不能只相信表單已清理。
    execute('UPDATE notifications SET digest_pending=1 WHERE user_id=?', (uid,))
    result = app_module.send_daily_digests()
    assert result['users_sent'] == 0 and result['notifications_sent'] == 0
    assert mail == []
    row = q('SELECT digest_pending, message FROM notifications WHERE user_id=?',
            (uid,), one=True)
    assert row['digest_pending'] == 0
    assert row['message'] == '退訂後不得寄出的摘要', '取消 Email 不得刪除站內通知'


def test_daily_digest_batches_and_is_idempotent(mail):
    uid, email = _user('daily', 'daily')
    app_module.notify(uid, None, '摘要第一則')
    app_module.notify(uid, None, '摘要第二則')
    assert mail == []
    assert q('SELECT COUNT(*) c FROM notifications WHERE user_id=? AND digest_pending=1',
             (uid,), one=True)['c'] == 2

    result = app_module.send_daily_digests()
    assert result['users_sent'] == 1 and result['notifications_sent'] == 2
    assert len(mail) == 1 and mail[0][0] == email
    assert '每日通知摘要' in mail[0][1]
    assert '摘要第一則' in mail[0][2] and '摘要第二則' in mail[0][2]
    rows = q('SELECT digest_pending, digest_sent_at, digest_claim FROM notifications '
             'WHERE user_id=?', (uid,))
    assert all(r['digest_pending'] == 0 and r['digest_sent_at'] and not r['digest_claim']
               for r in rows)

    assert app_module.send_daily_digests()['notifications_sent'] == 0
    assert len(mail) == 1


def test_digest_failure_retains_pending_and_releases_claim(mail, monkeypatch):
    uid, _ = _user('retry', 'daily')
    app_module.notify(uid, None, '失敗後要重試')
    monkeypatch.setattr(app_module, 'send_email', lambda *args: False)
    result = app_module.send_daily_digests()
    assert result['users_failed'] == 1
    row = q('SELECT digest_pending, digest_sent_at, digest_claim FROM notifications '
            'WHERE user_id=?', (uid,), one=True)
    assert row['digest_pending'] == 1 and row['digest_sent_at'] is None
    assert row['digest_claim'] is None

    monkeypatch.setattr(app_module, 'send_email', lambda *args: mail.append(args) or True)
    assert app_module.send_daily_digests()['users_sent'] == 1
    assert len(mail) == 1
    assert q('SELECT digest_pending FROM notifications WHERE user_id=?', (uid,),
             one=True)['digest_pending'] == 0


def test_digest_claim_skips_fresh_and_recycles_stale(mail):
    uid, _ = _user('claim', 'daily')
    app_module.notify(uid, None, '認領測試')
    execute("UPDATE notifications SET digest_claim='other-worker', digest_claimed_at=? "
            'WHERE user_id=?', ('2099-01-01 00:00:00', uid))
    now = datetime.datetime(2099, 1, 1, 0, 15)
    assert app_module.send_daily_digests(now=now)['users_sent'] == 0
    assert mail == []

    execute("UPDATE notifications SET digest_claimed_at='2000-01-01 00:00:00' WHERE user_id=?",
            (uid,))
    result = app_module.send_daily_digests(now=now)
    assert result['reclaimed'] == 1 and result['users_sent'] == 1
    assert len(mail) == 1


def test_send_digests_cli_disabled_keeps_pending():
    uid, _ = _user('clioff', 'daily')
    app_module.notify(uid, None, 'SMTP 未配置')
    result = app.test_cli_runner().invoke(args=['send-digests'])
    assert result.exit_code == 0 and 'SMTP 尚未配置' in result.output
    assert q('SELECT digest_pending FROM notifications WHERE user_id=?', (uid,),
             one=True)['digest_pending'] == 1


def test_send_digests_cli_sends(mail):
    # 測試共用資料庫；先清掉前一案例刻意保留的待送摘要，驗證 CLI 的全站批次結果。
    execute('DELETE FROM notifications WHERE digest_pending=1')
    uid, _ = _user('cliok', 'daily')
    app_module.notify(uid, None, 'CLI 摘要')
    result = app.test_cli_runner().invoke(args=['send-digests'])
    assert result.exit_code == 0 and '成功 1 人／1 則' in result.output
    assert len(mail) == 1


def test_security_email_ignores_general_unsubscribe(mail):
    uid, email = _user('security', 'off')
    response = app.test_client().post('/forgot', data={'email': email})
    assert response.status_code == 302
    assert any(m[0] == email and '重設密碼' in m[1] for m in mail)


def test_nested_atomic_waits_for_outer_commit(mail):
    uid, email = _user('nestedok')
    with app_module.atomic():
        with app_module.atomic():
            app_module.notify(uid, None, '巢狀交易通知')
            assert mail == []
        assert mail == [], '內層 atomic 結束時外層仍未提交，不應寄信'
    assert [m[0] for m in mail] == [email]


def test_nested_atomic_outer_rollback_discards_mail(mail):
    uid, _ = _user('nestedfail')
    with pytest.raises(RuntimeError):
        with app_module.atomic():
            with app_module.atomic():
                app_module.notify(uid, None, '外層回滾通知')
            assert mail == []
            raise RuntimeError('outer rollback')
    assert mail == []
    assert q('SELECT 1 FROM notifications WHERE user_id=?', (uid,)) == []


def test_attachment_removed_when_database_insert_fails(monkeypatch):
    uid, _ = _user('orphan')
    client = _login(uid)
    real_execute = app_module.execute

    def fail_leave_insert(sql, args=()):
        if sql.startswith('INSERT INTO leave_requests'):
            raise RuntimeError('simulated database failure')
        return real_execute(sql, args)

    monkeypatch.setattr(app_module, 'execute', fail_leave_insert)
    uploads = Path(app_module.UPLOAD_DIR)
    before = set(uploads.glob('*'))
    day = datetime.date.today() + datetime.timedelta(days=1200)
    with pytest.raises(RuntimeError, match='simulated database failure'):
        client.post('/new', data={
            'leave_type_id': '1', 'start_date': day.isoformat(), 'end_date': day.isoformat(),
            'start_period': 'AM', 'end_period': 'PM', 'reason': 'v26 寫庫失敗附件補償',
            'contact_phone': '13800000001',
            'attachment': (io.BytesIO(b'%PDF-1.4\nproof'), 'proof.pdf')},
            content_type='multipart/form-data')
    assert set(uploads.glob('*')) == before
    assert q("SELECT 1 FROM leave_requests WHERE reason='v26 寫庫失敗附件補償'") == []
