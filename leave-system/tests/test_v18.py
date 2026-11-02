# -*- coding: utf-8 -*-
"""v1.8.0 測試：單一登入（SSO / OIDC，GAP-ANALYSIS A2）。

守住四件事：

    1. 組態門控：未設定四個必要參數時 sso_configured() 為 False，登入頁不顯示
       SSO 按鈕、/login/sso 直接導回登入並閃錯。
    2. 授權碼流程：設定後 /login/sso 跳到 IdP 授權網址；/callback 走完整
       state 校驗 → code 兌換 → userinfo → resolve_user → 建立 session。
       網路呼叫以 monkeypatch 替換，不需真實 IdP。
    3. 身分對映：sub 命中 external_id 最優先；username claim 次之；email
       自動連結可關；明確開啟 SSO_PROVISION 才自動開通新帳號（且只能走 SSO）。
    4. 本地登入永遠保留為災難備援——SSO 掛掉時管理員仍能以帳密進系統。

沿用 conftest 的共用臨時資料庫；自建 v18_* 帳號避免與其他測試檔搶種子資料。
"""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute  # noqa: E402
from sso import (sso_settings, sso_configured, authorize_url,  # noqa: E402
                 exchange_code, fetch_userinfo, resolve_user,
                 _PROVISION_PW_HASH)
from utils import APP_VERSION  # noqa: E402

DEMO = 'Demo@2026'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def sso_on(monkeypatch):
    """在 app.config 裡開啟 SSO（不動環境變數，保持離線）。"""
    for k, v in {
        'SSO_CLIENT_ID': 'cid',
        'SSO_AUTHORIZE_URL': 'https://idp.example.com/authorize',
        'SSO_TOKEN_URL': 'https://idp.example.com/token',
        'SSO_USERINFO_URL': 'https://idp.example.com/userinfo',
        'SSO_REDIRECT_URI': 'http://localhost/callback',
        'SSO_SCOPES': 'openid email profile',
        'SSO_USERNAME_CLAIM': 'preferred_username',
        'SSO_ROLE_CLAIM': 'role',
        'SSO_DEFAULT_ROLE': 'student',
        'SSO_PROVISION': '0',
        'SSO_AUTO_LINK_EMAIL': '1',
    }.items():
        monkeypatch.setitem(app.config, k, v)
    return app


def _add_user(username, email=None, external_id=None, role='student'):
    execute('DELETE FROM users WHERE username=?', (username,))
    new_id = execute(
        'INSERT INTO users (username, password_hash, name, role, department, '
        'email, external_id, active) VALUES (?,?,?,?,?,?,?,1)',
        (username, 'x', username, role, '', email or '', external_id))
    return q('SELECT * FROM users WHERE id=?', (new_id,), one=True)


def _stub_network(monkeypatch, claims):
    """把 sso 的兩個網路 seam 換成回傳假 token / claims 的 stub。"""
    monkeypatch.setattr('sso._http_post_json',
                        lambda *a, **k: {'access_token': 'tok'})
    monkeypatch.setattr('sso._http_get_json', lambda *a, **k: dict(claims))


# --------------------------------------------------------------------------
# 版本與組態門控
# --------------------------------------------------------------------------
def test_version_is_1_8_0():
    assert APP_VERSION == '1.8.0'


def test_sso_not_configured_by_default():
    assert sso_configured() is False


def test_login_page_hides_sso_button_by_default():
    c = app.test_client()
    assert '使用學校帳號登入' not in c.get('/login').get_data(as_text=True)


def test_login_sso_redirects_when_disabled():
    c = app.test_client()
    r = c.get('/login/sso')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# --------------------------------------------------------------------------
# 授權網址組裝
# --------------------------------------------------------------------------
def test_authorize_url_built(sso_on):
    u = authorize_url('st123')
    assert u.startswith('https://idp.example.com/authorize?')
    assert 'client_id=cid' in u
    assert 'state=st123' in u
    assert 'response_type=code' in u


# --------------------------------------------------------------------------
# 路由層：開啟後的行為
# --------------------------------------------------------------------------
def test_login_sso_redirects_to_idp_when_enabled(sso_on):
    c = app.test_client()
    r = c.get('/login/sso')
    assert r.status_code == 302
    loc = r.headers['Location']
    assert loc.startswith('https://idp.example.com/authorize')
    with c.session_transaction() as s:
        assert s.get('sso_state')  # state 已存入 session


def test_login_page_shows_sso_button_when_configured(sso_on):
    c = app.test_client()
    assert '使用學校帳號登入' in c.get('/login').get_data(as_text=True)


def test_callback_bad_state(sso_on):
    c = app.test_client()
    with c.session_transaction() as s:
        s['sso_state'] = 'real-state'
    r = c.get('/callback?state=wrong&code=x')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_callback_missing_code(sso_on):
    c = app.test_client()
    with c.session_transaction() as s:
        s['sso_state'] = 'real-state'
    r = c.get('/callback?state=real-state')  # 缺 code
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_callback_exchange_failure_redirects(sso_on, monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError('boom')  # noqa: F821
    monkeypatch.setattr('sso._http_post_json', _boom)
    c = app.test_client()
    with c.session_transaction() as s:
        s['sso_state'] = 'real-state'
    r = c.get('/callback?state=real-state&code=x')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_callback_success_establishes_session(sso_on, monkeypatch):
    u = _add_user('v18_sso', email='v18_sso@school.edu', external_id='sub-xyz')
    _stub_network(monkeypatch, {'sub': 'sub-xyz',
                                'preferred_username': 'v18_sso',
                                'email': 'v18_sso@school.edu'})
    c = app.test_client()
    with c.session_transaction() as s:
        s['sso_state'] = 'state-abc'
    r = c.get('/callback?state=state-abc&code=code123')
    assert r.status_code == 302
    # 儀表板路由掛在 '/'（見 app.py @app.route('/') def dashboard），故導向 '/'
    assert r.headers['Location'] in ('/', '/?')
    # session 已建立，能直接進首頁（即儀表板）
    assert c.get('/').status_code == 200
    with c.session_transaction() as s:
        assert s.get('uid') == u['id']


def test_local_login_still_works_with_sso_configured(sso_on):
    c = app.test_client()
    r = c.post('/login', data={'username': 'admin', 'password': DEMO})
    assert r.status_code == 302
    assert c.get('/').status_code == 200


# --------------------------------------------------------------------------
# 身分對映：resolve_user 的優先序
# --------------------------------------------------------------------------
def test_resolve_by_sub_first(sso_on):
    u = _add_user('v18_sub', external_id='ext-1')
    user, err = resolve_user({'sub': 'ext-1', 'email': 'nope@example.com'})
    assert err is None
    assert user['id'] == u['id']


def test_resolve_by_username_claim(sso_on):
    u = _add_user('v18_usr', email='u1@school.edu')
    user, err = resolve_user({'sub': '', 'preferred_username': 'v18_usr',
                              'email': 'u1@school.edu'})
    assert err is None
    assert user['id'] == u['id']


def test_resolve_email_autolink_sets_external_id(sso_on):
    u = _add_user('v18_link', email='u2@school.edu')
    user, err = resolve_user({'sub': 'ext-2', 'preferred_username': 'ghost',
                              'email': 'u2@school.edu'})
    assert err is None
    assert user['id'] == u['id']
    # 自動綁定 external_id 以利日後直接命中
    assert q('SELECT external_id FROM users WHERE id=?', (u['id'],),
             one=True)['external_id'] == 'ext-2'


def test_resolve_email_link_disabled(sso_on, monkeypatch):
    monkeypatch.setitem(app.config, 'SSO_AUTO_LINK_EMAIL', '0')
    _add_user('v18_nolink', email='u3@school.edu')
    user, err = resolve_user({'sub': '', 'preferred_username': 'x',
                              'email': 'u3@school.edu'})
    assert user is None
    assert err


def test_resolve_provision_off_no_match(sso_on):
    user, err = resolve_user({'sub': '', 'preferred_username': 'fresh',
                              'email': 'fresh@school.edu'})
    assert user is None
    assert err


def test_resolve_provision_on_creates_user(sso_on, monkeypatch):
    monkeypatch.setitem(app.config, 'SSO_PROVISION', '1')
    user, err = resolve_user({'sub': 'ext-fresh', 'preferred_username': 'fresh',
                              'email': 'fresh@school.edu', 'name': 'Fresh User',
                              'role': 'faculty'})
    assert err is None
    assert user['external_id'] == 'ext-fresh'
    assert user['role'] == 'faculty'
    # 自動開通的新帳號只能走 SSO：密碼雜湊是隨機強雜湊，本地無法登入
    assert user['password_hash'] == _PROVISION_PW_HASH
    uid = user['id']
    assert q('SELECT id FROM users WHERE username=? AND active=1',
             ('fresh',), one=True)['id'] == uid


def test_resolve_provision_role_fallback(sso_on, monkeypatch):
    monkeypatch.setitem(app.config, 'SSO_PROVISION', '1')
    user, _ = resolve_user({'sub': 'ext-r', 'preferred_username': 'r',
                            'email': 'r@school.edu', 'role': 'bogus_role'})
    assert user['role'] == 'student'  # 回落到預設角色
