# -*- coding: utf-8 -*-
"""單一登入（SSO / OIDC）整合層（v1.8.0 · A2）。

GAP-ANALYSIS A2 指出：大學都有 Entra ID / CAS / SAML / 微信校園號，自帶一套
帳密是採用最大阻力。本模組把身分中介層插在 `login()` 之前——本地 `users`
表只多存一個 `external_id` 對映，本地密碼登入保留為**災難備援**。

設計上的誠實邊界（與 C1 課表真實化一致）：

- 本模組交付的是**接線通道**：標準 OIDC 授權碼流程（authorize → code →
  token → userinfo），全部由組態驅動，且網路呼叫都隔成可替換的 seam，
  因此能在沒有真實 IdP 的情況下被單元測試完整覆蓋。
- **真實 IdP 的連線參數**（issuer、client_id、重定向 URI、scope）與**使用者／
  角色對映規則**屬於各校的部署設定，本倉不內含任何一家學校的憑證或假設，
  留給校方在部署時填入（見 README「單一登入（SSO）」一節）。
- 生產環境還應補：ID-token 簽章驗證（本版只取 userinfo，未驗 JWT 簽章）、
  nonce 重放防護、群組／角色聲明到本地角色的正式對映。這些標註在程式碼與
  README 裡，待校方對接時一併補齊。

不引入任何第三方依賴：網路呼叫只用標準函式庫 `urllib`，方便 CI 與單測。
"""

import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from werkzeug.security import generate_password_hash

# 組態讀取優先級：Flask app.config（測試／部署可注入）＞ 環境變數。
try:  # 在 app context 外（如純單測載入）也不要報錯
    from flask import current_app
except Exception:  # pragma: no cover - flask 一定在
    current_app = None


def _cfg(key, default=''):
    """組態：app.config 優先，否則退回環境變數。"""
    try:
        if current_app is not None:
            v = current_app.config.get(key)
            if v is not None:
                return v
    except Exception:
        pass
    return os.environ.get(key, default)


def _bool(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


# --------------------------------------------------------------------------
# 組態介面
# --------------------------------------------------------------------------
def sso_settings():
    """回傳目前 SSO 組態（dict）。全部可經環境變數或 app.config 注入。"""
    return {
        'client_id': _cfg('SSO_CLIENT_ID'),
        'client_secret': _cfg('SSO_CLIENT_SECRET'),
        'authorize_url': _cfg('SSO_AUTHORIZE_URL'),
        'token_url': _cfg('SSO_TOKEN_URL'),
        'userinfo_url': _cfg('SSO_USERINFO_URL'),
        'redirect_uri': _cfg('SSO_REDIRECT_URI'),
        'scopes': _cfg('SSO_SCOPES', 'openid email profile'),
        'username_claim': _cfg('SSO_USERNAME_CLAIM', 'email'),
        'role_claim': _cfg('SSO_ROLE_CLAIM', ''),
        'default_role': _cfg('SSO_DEFAULT_ROLE', 'student'),
        'provision': _bool(_cfg('SSO_PROVISION', '0')),
        'auto_link_email': _bool(_cfg('SSO_AUTO_LINK_EMAIL', '1')),
    }


def sso_configured():
    """SSO 是否已啟用：四個必要端點／參數齊全才算開啟。"""
    s = sso_settings()
    return bool(s['client_id'] and s['authorize_url']
                and s['token_url'] and s['userinfo_url'])


def authorize_url(state, redirect_uri=None):
    """組出跳轉到 IdP 的授權網址（授權碼流程）。"""
    s = sso_settings()
    ru = redirect_uri or s['redirect_uri'] or ''
    params = {
        'response_type': 'code',
        'client_id': s['client_id'],
        'redirect_uri': ru,
        'scope': s['scopes'],
        'state': state,
    }
    return s['authorize_url'] + '?' + urllib.parse.urlencode(params)


# --------------------------------------------------------------------------
# 網路 seam：單測時以 monkeypatch 替換，不必真的打 IdP
# --------------------------------------------------------------------------
def _http_post_json(url, data, headers=None):
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode('utf-8'),
        headers={'Accept': 'application/json',
                 'Content-Type': 'application/x-www-form-urlencoded',
                 **(headers or {})},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


def _http_get_json(url, bearer):
    req = urllib.request.Request(
        url,
        headers={'Authorization': f'Bearer {bearer}',
                 'Accept': 'application/json'},
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode('utf-8'))


# --------------------------------------------------------------------------
# 授權碼兌換與 userinfo（網路 seam 以上兩個函式為界）
# --------------------------------------------------------------------------
def exchange_code(code, redirect_uri=None):
    """拿授權碼去 IdP 換 access_token。"""
    s = sso_settings()
    ru = redirect_uri or s['redirect_uri'] or ''
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': ru,
        'client_id': s['client_id'],
        'client_secret': s['client_secret'],
    }
    return _http_post_json(s['token_url'], data)


def fetch_userinfo(access_token):
    """拿 access_token 去 userinfo 端點取身分聲明。"""
    return _http_get_json(sso_settings()['userinfo_url'], access_token)


# --------------------------------------------------------------------------
# 身分對映：claims → 本地使用者
# --------------------------------------------------------------------------
# 自動開通的新使用者沒有「真實密碼」——給一個強隨機雜湊，使其無法用本地密碼
# 登入（只能走 SSO）。這同時也是「災難備援」語意：若未開啟 provision，
# 這種帳號根本不會被建立。
_PROVISION_PW_HASH = generate_password_hash(secrets.token_urlsafe(32))

_VALID_ROLES = ('student', 'advisor', 'faculty', 'admin')


def resolve_user(claims):
    """把 OIDC claims 對映到本地使用者。

    順序：① external_id → ② username claim → ③ email（可關） → ④ 自動開通。
    回傳 (user_dict 或 None, 錯誤訊息或 None)。
    """
    from db import q, execute  # 延遲匯入：本模組也要能被純單測載入

    s = sso_settings()
    username = (claims.get(s['username_claim']) or '').strip()
    sub = (claims.get('sub') or '').strip()
    email = (claims.get('email') or '').strip()

    # ① 已綁定 external_id 者直接命中（最穩，不隨 email 改變而失效）
    if sub:
        u = q('SELECT * FROM users WHERE external_id=? AND active=1',
              (sub,), one=True)
        if u:
            return u, None
    # ② username claim（學號／教工號）
    if username:
        u = q('SELECT * FROM users WHERE username=? AND active=1',
              (username,), one=True)
        if u:
            if sub and not u['external_id']:
                execute('UPDATE users SET external_id=? WHERE id=?', (sub, u['id']))
            return u, None
    # ③ 用 email 自動連結既有本地帳號（預設開啟；可關）
    if s['auto_link_email'] and email:
        u = q('SELECT * FROM users WHERE email=? AND active=1',
              (email,), one=True)
        if u:
            if sub and not u['external_id']:
                execute('UPDATE users SET external_id=? WHERE id=?', (sub, u['id']))
            return u, None
    # ④ 自動開通：只有明確開啟 SSO_PROVISION 才建立新帳號
    if s['provision'] and username:
        role = claims.get(s['role_claim']) or s['default_role']
        if role not in _VALID_ROLES:
            role = s['default_role']
        name = (claims.get('name') or username).strip()
        new_id = execute(
            'INSERT INTO users (username, password_hash, name, role, department, '
            'email, external_id, active) VALUES (?,?,?,?,?,?,?,1)',
            (username, _PROVISION_PW_HASH, name, role, '', email, sub or None))
        u = q('SELECT * FROM users WHERE id=?', (new_id,), one=True)
        return u, None
    return None, 'no matching user (auto-provision disabled)'
