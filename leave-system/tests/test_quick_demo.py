# -*- coding: utf-8 -*-
"""Regression tests for the bilingual quick role-card demo.

These tests do not change settings at import time. Instead, they monkeypatch the
module-level flag read by each route, so the shared conftest database remains valid.
"""
import app as app_module
from db import q


def _client():
    return app_module.app.test_client()


def _user_id(username):
    with app_module.app.app_context():
        row = q("SELECT id FROM users WHERE username=? AND active=1", (username,), one=True)
        assert row is not None
        return row["id"]


def test_quick_login_defaults_off(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_QUICK_LOGIN", False)
    client = _client()

    resp = client.get("/login")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Account Login / 帳號登入" in text
    assert "Quick Demo Login" not in text

    resp = client.post("/login/demo-role", data={"role": "student"})
    assert resp.status_code == 404


def test_quick_login_page_is_bilingual(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_QUICK_LOGIN", True)
    client = _client()

    resp = client.get("/login")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Quick Demo Login" in text
    assert "快速演示登入" in text
    assert "Click a role card" in text
    assert "點選角色卡" in text
    for en, zh in [
        ("Student", "學生"),
        ("Advisor", "導師"),
        ("Faculty", "院系"),
        ("Admin", "管理員"),
    ]:
        assert en in text
        assert zh in text
    assert "Password login / 使用帳號密碼登入" in text


def test_role_card_creates_expected_session(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_QUICK_LOGIN", True)
    client = _client()

    expected = {
        "student": ("/", _user_id("2024001")),
        "advisor": ("/approvals", _user_id("T001")),
        "faculty": ("/approvals", _user_id("F001")),
        "admin": ("/admin/users", _user_id("admin")),
    }
    for role, (target, uid) in expected.items():
        client = _client()
        resp = client.post("/login/demo-role", data={"role": role})
        assert resp.status_code == 302
        assert resp.headers["Location"] == target
        with client.session_transaction() as sess:
            assert sess["uid"] == uid
            assert "csrf_token" in sess
            assert "auth_at" in sess


def test_invalid_role_does_not_log_in(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_QUICK_LOGIN", True)
    client = _client()

    resp = client.post("/login/demo-role", data={"role": "superadmin"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert "uid" not in sess


def test_password_login_still_available_in_quick_mode(monkeypatch):
    monkeypatch.setattr(app_module, "DEMO_QUICK_LOGIN", True)
    client = _client()

    resp = client.get("/login/password")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Account Login / 帳號登入" in text
    assert 'name="password"' in text
