# -*- coding: utf-8 -*-
"""v1.12.0 測試：自助密碼重設與 Email 驗證（GAP-ANALYSIS B1/B2）。

守住幾件事：
  1. /forgot 對已登記／未登記的 Email 一律回同一句泛用訊息（帳號枚舉防護）；
  2. SMTP 未配置時通道自動關閉——不產生 token、行為與 v1.11.0 一致；
  3. 配置 SMTP 後：重設信含一次性連結；開信 → 設新密碼 → 舊密碼失效、
     email_verified 轉 1、token 只能用一次、留稽核；
  4. token 過期／重放／弱新密碼／與目前密碼相同都被擋；
  5. 同一帳號的重設信申請有頻率上限（防郵件轟炸）；
  6. 管理端新增帳號（填 Email）會寄驗證信；/verify/<token> 標記已驗證。

郵件用 monkeypatch 假替 send_email 捕捉內容，不依賴真實 SMTP。
"""
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app as app_module  # noqa: E402
from app import app, q, execute  # noqa: E402
from utils import APP_VERSION  # noqa: E402

PW = 'V22test@2026'
STU = 'v22_stu'
STU_EMAIL = f'{STU}@example.edu'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _no_smtp(monkeypatch):
    """每個測試預設清掉 SMTP 配置，需要時再自行 setenv。"""
    for k in ('LEAVE_SMTP_HOST', 'LEAVE_SMTP_USER', 'LEAVE_SMTP_FROM'):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """重設頻率上限回預設（5），需要更低值時由測試自行覆寫。"""
    monkeypatch.setattr(app_module, 'RESET_MAX_PER_HOUR', 5)


@pytest.fixture()
def smtp_mail(monkeypatch):
    """配置 SMTP 環境變數並以 list 捕捉寄出的 (to, subject, body)。"""
    monkeypatch.setenv('LEAVE_SMTP_HOST', 'smtp.test')
    monkeypatch.setenv('LEAVE_SMTP_USER', 'noreply@test')
    monkeypatch.setenv('LEAVE_SMTP_FROM', 'noreply@test')
    sent = []

    def _fake_send(to_addr, subject, body):
        sent.append((to_addr, subject, body))

    monkeypatch.setattr(app_module, 'send_email', _fake_send)
    return sent


@pytest.fixture()
def make_student():
    """工廠 fixture：建立專用學生（導師 T001），回傳 (username, password)。"""
    def _make(username, password):
        t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
        admin = app.test_client()
        admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
        if not q('SELECT id FROM users WHERE username=?', (username,), one=True):
            r = admin.post('/admin/users/add', data={
                'username': username, 'password': password, 'name': f'{username} 測試生',
                'role': 'student', 'department': '人工智能學域',
                'email': f'{username}@example.edu', 'advisor_id': str(t1)})
            assert r.status_code == 200, f'建立 {username} 失敗'
        return username, password
    return _make


@pytest.fixture()
def student(make_student):
    return make_student(STU, PW)


def _stu_id(username=STU):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _reset_tokens(user_id):
    return q("SELECT * FROM password_resets WHERE user_id=? AND purpose='reset'",
             (user_id,))


def _extract_reset_token(body):
    m = re.search(r'/reset/([A-Za-z0-9_\-]+)', body)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------
def test_version_is_at_least_1_12_0():
    """版本只設下限，避免日後每次發版都要回來改這條斷言。"""
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 12, 0)


# --------------------------------------------------------------------------
# /forgot：帳號枚舉防護
# --------------------------------------------------------------------------
def test_unknown_email_gets_generic_response_no_token():
    c = app.test_client()
    r = c.post('/forgot', data={'email': 'nobody@nowhere.example'})
    assert r.status_code == 302
    page = c.get('/login').get_data(as_text=True)
    assert '若此 Email 已登記' in page
    assert q("SELECT COUNT(*) c FROM password_resets WHERE purpose='reset'",
             one=True)['c'] == 0
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='forgot_password' "
             "AND detail LIKE '%nobody@nowhere%'", one=True)['c'] == 0


def test_known_email_without_smtp_creates_no_token(student):
    c = app.test_client()
    r = c.post('/forgot', data={'email': STU_EMAIL})
    assert r.status_code == 302
    page = c.get('/login').get_data(as_text=True)
    assert '若此 Email 已登記' in page          # 泛用訊息：SMTP 未配置也不洩漏差異
    assert _reset_tokens(_stu_id()) == []


# --------------------------------------------------------------------------
# 完整重設流程
# --------------------------------------------------------------------------
def test_full_reset_flow(student, smtp_mail):
    uid = _stu_id()
    c = app.test_client()
    r = c.post('/forgot', data={'email': STU_EMAIL})
    assert r.status_code == 302
    # 郵件寄出、內容含連結；稽核留痕
    assert len(smtp_mail) == 1
    to, subject, body = smtp_mail[0]
    assert to == STU_EMAIL
    assert '重設密碼' in subject
    token = _extract_reset_token(body)
    assert token
    # 稽核留痕（前面的枚舉防護測試對同一 Email 也留過痕，故 >= 1）
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='forgot_password' "
             f"AND detail LIKE '%{STU_EMAIL}%'", one=True)['c'] >= 1
    # 開信點連結
    r = c.get(f'/reset/{token}')
    assert r.status_code == 200
    assert '重設密碼' in r.get_data(as_text=True)
    # 設新密碼
    new_pw = 'R3set@2026x'
    r = c.post(f'/reset/{token}',
               data={'new_password': new_pw, 'confirm_password': new_pw})
    assert r.status_code == 302
    row = q('SELECT password_hash, pwd_changed_at, email_verified FROM users '
            'WHERE id=?', (uid,), one=True)
    assert row['pwd_changed_at'] is not None
    assert row['email_verified'] == 1
    # 舊密碼登入失敗、新密碼成功
    c2 = app.test_client()
    r = c2.post('/login', data={'username': STU, 'password': PW}, follow_redirects=True)
    assert '帳號或密碼錯誤' in r.get_data(as_text=True)
    c3 = app.test_client()
    r = c3.post('/login', data={'username': STU, 'password': new_pw})
    assert r.status_code == 302
    # token 只能用一次
    c4 = app.test_client()
    r = c4.get(f'/reset/{token}', follow_redirects=True)
    assert '無效或已逾期' in r.get_data(as_text=True)
    assert q('SELECT used_at FROM password_resets WHERE user_id=? '
             "AND purpose='reset'", (uid,), one=True)['used_at'] is not None
    # 自助重設留稽核
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='reset_password' "
             "AND detail LIKE '%自助%'", one=True)['c'] >= 1


def test_reset_rejects_same_and_weak_passwords(make_student, smtp_mail):
    uname, pw = make_student('v22_stu3', 'V22keep@2026')
    c = app.test_client()
    c.post('/forgot', data={'email': f'{uname}@example.edu'})
    # 建號時（SMTP 已配置）會先收到一封驗證信，取「重設密碼」那封
    reset_mails = [m for m in smtp_mail if '重設密碼' in m[1]]
    _, _, body = reset_mails[0]
    token = _extract_reset_token(body)
    # 與目前密碼相同 → 拒絕
    r = c.post(f'/reset/{token}',
               data={'new_password': pw, 'confirm_password': pw})
    page = r.get_data(as_text=True)
    assert '新密碼不可與目前密碼相同' in page
    # 弱密碼 → 拒絕
    r = c.post(f'/reset/{token}',
               data={'new_password': 'short', 'confirm_password': 'short'})
    assert '密碼長度至少需' in r.get_data(as_text=True)
    assert q('SELECT used_at FROM password_resets WHERE token_hash=?',
             (app_module._hash_token(token),), one=True)['used_at'] is None


def test_expired_token_rejected(student):
    raw = app_module.issue_token(_stu_id(), 'reset', minutes=1)
    execute("UPDATE password_resets SET expires_at='2000-01-01 00:00:00' "
            'WHERE token_hash=?', (app_module._hash_token(raw),))
    c = app.test_client()
    r = c.get(f'/reset/{raw}', follow_redirects=True)
    assert '無效或已逾期' in r.get_data(as_text=True)


# --------------------------------------------------------------------------
# 頻率限制：防郵件轟炸
# --------------------------------------------------------------------------
def test_reset_rate_limit(make_student, smtp_mail, monkeypatch):
    uname, pw = make_student('v22_stu4', PW)
    smtp_mail.clear()                                # 排掉建號時的驗證信
    monkeypatch.setattr(app_module, 'RESET_MAX_PER_HOUR', 1)
    c = app.test_client()
    c.post('/forgot', data={'email': f'{uname}@example.edu'})
    c.post('/forgot', data={'email': f'{uname}@example.edu'})
    assert len(smtp_mail) == 1                       # 第二次被限流壓下
    assert len(_reset_tokens(_stu_id(uname))) == 1


# --------------------------------------------------------------------------
# Email 驗證（B2）
# --------------------------------------------------------------------------
def test_verify_email_flow(make_student):
    # 專用帳號：full_reset_flow 成功重設會把 v22_stu 的 email_verified 轉 1
    uname, pw = make_student('v22_stu5', PW)
    uid = _stu_id(uname)
    assert q('SELECT email_verified FROM users WHERE id=?', (uid,),
             one=True)['email_verified'] == 0
    raw = app_module.issue_token(uid, 'verify')
    c = app.test_client()
    r = c.get(f'/verify/{raw}', follow_redirects=True)
    assert 'Email 驗證完成' in r.get_data(as_text=True)
    assert q('SELECT email_verified FROM users WHERE id=?', (uid,),
             one=True)['email_verified'] == 1
    # 重放同一連結 → 無效
    c2 = app.test_client()
    r = c2.get(f'/verify/{raw}', follow_redirects=True)
    assert '無效或已逾期' in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='verify_email'",
             one=True)['c'] >= 1


def test_add_user_sends_verification_mail(make_student, smtp_mail):
    uname, pw = make_student('v22_stu2', PW)
    verify_mails = [m for m in smtp_mail if '/verify/' in m[2]]
    assert verify_mails, '新增帳號應寄出驗證信'
    uid = _stu_id(uname)
    assert q("SELECT COUNT(*) c FROM password_resets WHERE user_id=? AND "
             "purpose='verify'", (uid,), one=True)['c'] == 1


# --------------------------------------------------------------------------
# 稽核標籤
# --------------------------------------------------------------------------
def test_audit_labels_registered():
    from app import AUDIT_ACTION_LABEL
    assert AUDIT_ACTION_LABEL.get('forgot_password') == '申請重設密碼'
    assert AUDIT_ACTION_LABEL.get('verify_email') == '驗證 Email'
