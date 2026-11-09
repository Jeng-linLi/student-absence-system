# -*- coding: utf-8 -*-
"""v1.19.1：安全郵件的絕對連結不得信任請求 Host。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app as app_module  # noqa: E402
from app import app, q  # noqa: E402


FIXED_BASE = 'https://leave.school.example'
PW = 'V27test@2026'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _fixed_base(monkeypatch):
    monkeypatch.setattr(app_module, 'RESET_BASE_URL', FIXED_BASE)


@pytest.fixture()
def smtp_mail(monkeypatch):
    monkeypatch.setenv('LEAVE_SMTP_HOST', 'smtp.test')
    monkeypatch.setenv('LEAVE_SMTP_USER', 'noreply@test')
    monkeypatch.setenv('LEAVE_SMTP_FROM', 'noreply@test')
    sent = []

    def _fake_send(to_addr, subject, body):
        sent.append((to_addr, subject, body))

    monkeypatch.setattr(app_module, 'send_email', _fake_send)
    return sent


def _add_student(username, base_url=None):
    admin = app.test_client()
    kwargs = {'base_url': base_url} if base_url else {}
    advisor = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
    r = admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'}, **kwargs)
    assert r.status_code == 302
    r = admin.post('/admin/users/add', data={
        'username': username, 'password': PW, 'name': f'{username} 測試生',
        'role': 'student', 'department': '人工智能學域',
        'email': f'{username}@example.edu', 'advisor_id': str(advisor),
    }, **kwargs)
    assert r.status_code == 200


@pytest.mark.parametrize('raw, expected', [
    ('https://leave.school.example/', 'https://leave.school.example'),
    ('http://localhost:5055', 'http://localhost:5055'),
])
def test_base_url_accepts_only_normalized_http_origins(raw, expected):
    assert app_module._validated_base_url(raw) == expected


@pytest.mark.parametrize('raw', [
    'javascript:alert(1)',
    'ftp://leave.school.example',
    '//attacker.example',
    'https:///missing-host',
    'https://user:secret@leave.school.example',
    'https://leave.school.example/path',
    'https://leave.school.example/?next=attacker.example',
    'https://leave.school.example/#fragment',
    'https://leave.school.example:bad',
    'https://leave school.example',
])
def test_base_url_rejects_invalid_or_ambiguous_configuration(raw):
    with pytest.raises(ValueError):
        app_module._validated_base_url(raw)


def test_reset_mail_ignores_attacker_host(smtp_mail):
    _add_student('v27_reset_host')
    smtp_mail.clear()  # 排除建號驗證信，只檢查自助重設信。
    c = app.test_client()
    r = c.post('/forgot', data={'email': 'v27_reset_host@example.edu'},
               base_url='http://attacker.example')
    assert r.status_code == 302
    assert len(smtp_mail) == 1
    body = smtp_mail[0][2]
    assert f'{FIXED_BASE}/reset/' in body
    assert 'attacker.example' not in body


def test_verification_mail_ignores_attacker_host(smtp_mail):
    _add_student('v27_verify_host', base_url='http://attacker.example')
    verify_mails = [mail for mail in smtp_mail if '/verify/' in mail[2]]
    assert len(verify_mails) == 1
    body = verify_mails[0][2]
    assert f'{FIXED_BASE}/verify/' in body
    assert 'attacker.example' not in body
