# -*- coding: utf-8 -*-
"""v0.8.0 認證與帳號管理加固的測試。

涵蓋：登入限流持久化、帳號枚舉時序均化、密碼重設後舊 session 失效、
閒置 session 逾時、管理員停用保護、角色／帳號格式白名單。

注意：各測試共用同一份種子資料（conftest 建立），改過密碼的測試請使用
其他測試不會再次登入的帳號，避免互相干擾。
"""
import time

import pytest

from app import app, q, execute, toggle_user_error
from utils import valid_role, valid_username


@pytest.fixture(autouse=True)
def _app_ctx():
    """測試內直接呼叫 q/execute 等需要 app context 的函式。"""
    with app.app_context():
        yield


def _login(client, username, password):
    return client.post('/login', data={'username': username, 'password': password})


def _new_client():
    return app.test_client()


# --------------------------------------------------------------------------
# 登入限流（DB 持久化）
# --------------------------------------------------------------------------
def test_db_throttle_blocks_even_correct_password():
    c = _new_client()
    for _ in range(8):
        assert _login(c, 'throttle_user', 'WrongPass1').status_code == 200
    # 第 9 次即使密碼正確也應被 429 擋下
    r = _login(c, 'throttle_user', 'Pass@123')
    assert r.status_code == 429


def test_throttle_state_survives_new_client():
    """計數存在資料庫裡：換一個 test client（模擬另一個進程／重啟後的存取者）
    仍看得到同一份限流紀錄——舊版進程內 dict 做不到這一點。"""
    c1 = _new_client()
    for _ in range(8):
        _login(c1, 'persist_user', 'WrongPass1')
    c2 = _new_client()
    assert _login(c2, 'persist_user', 'Pass@123').status_code == 429


def test_throttle_window_expiry():
    """超過 LOGIN_WINDOW_SEC 的舊紀錄不再計入（indow 內的才計）。"""
    key = '127.0.0.1:window_user'
    old = time.time() - 3600          # 一小時前，早於 300 秒視窗
    for _ in range(8):
        execute('INSERT INTO login_attempts (key, ts) VALUES (?,?)', (key, old))
    c = _new_client()
    r = _login(c, 'window_user', 'Pass@123')
    assert r.status_code == 200        # 未被限流（密碼本身是錯的，但走的是正常失敗路徑）


# --------------------------------------------------------------------------
# 帳號枚舉時序均化
# --------------------------------------------------------------------------
def test_nonexistent_user_still_runs_hash_check(monkeypatch):
    """帳號不存在時也必須跑一次 check_password_hash，
    讓「帳號不存在」與「密碼錯誤」的回應時間無法區分。"""
    import app as app_mod
    calls = []
    orig = app_mod.check_password_hash

    def spy(hash_, pw):
        calls.append(pw)
        return orig(hash_, pw)

    monkeypatch.setattr(app_mod, 'check_password_hash', spy)
    c = _new_client()
    _login(c, 'no_such_user_xyz', 'Whatever1')
    assert len(calls) == 1          # dummy hash 比對跑了 exactly 一次
    # 存在的帳號 + 錯誤密碼：比對自己的 hash，一樣是一次
    calls.clear()
    _login(c, '2024001', 'WrongPass1')
    assert len(calls) == 1


# --------------------------------------------------------------------------
# 密碼重設 → 舊 session 失效
# --------------------------------------------------------------------------
def test_password_reset_invalidates_existing_session():
    student = _new_client()
    assert _login(student, '2024001', 'Pass@123').status_code == 302
    assert student.get('/').status_code == 200

    admin = _new_client()
    assert _login(admin, 'admin', 'Pass@123').status_code == 302
    uid = q("SELECT id FROM users WHERE username='2024001'", one=True)['id']
    r = admin.post(f'/admin/users/{uid}/reset-password', data={'password': 'Reset@2026x'})
    assert r.status_code == 302

    # 學生舊 session 應已失效：首頁被導回登入頁
    resp = student.get('/')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']
    # 用新密碼可重新登入
    assert _login(student, '2024001', 'Reset@2026x').status_code == 302


def test_self_password_change_sets_pwd_changed_at():
    c = _new_client()
    assert _login(c, '2024002', 'Pass@123').status_code == 302
    before = q("SELECT pwd_changed_at FROM users WHERE username='2024002'",
               one=True)['pwd_changed_at']
    r = c.post('/password', data={'old_password': 'Pass@123',
                                  'new_password': 'NewPass2026',
                                  'confirm_password': 'NewPass2026'})
    assert r.status_code == 302      # 成功後導向登出
    after = q("SELECT pwd_changed_at FROM users WHERE username='2024002'",
              one=True)['pwd_changed_at']
    assert after and after != before


def test_new_password_cannot_equal_old():
    c = _new_client()
    _login(c, '2024005', 'Pass@123')
    r = c.post('/password', data={'old_password': 'Pass@123',
                                  'new_password': 'Pass@123',
                                  'confirm_password': 'Pass@123'})
    assert '不可與目前密碼相同' in r.get_data(as_text=True)
    # 密碼沒被改動：舊密碼仍可登入
    c2 = _new_client()
    assert _login(c2, '2024005', 'Pass@123').status_code == 302


# --------------------------------------------------------------------------
# 閒置 session 逾時
# --------------------------------------------------------------------------
def test_idle_session_expires():
    c = _new_client()
    assert _login(c, '2024003', 'Pass@123').status_code == 302
    with c.session_transaction() as sess:
        sess['last_seen'] = '2020-01-01 00:00:00'      # 模擬閒置多年
    resp = c.get('/')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_fresh_session_survives():
    c = _new_client()
    assert _login(c, '2024003', 'Pass@123').status_code == 302
    assert c.get('/').status_code == 200


# --------------------------------------------------------------------------
# 管理後台保護
# --------------------------------------------------------------------------
def test_admin_cannot_disable_self():
    admin = _new_client()
    assert _login(admin, 'admin', 'Pass@123').status_code == 302
    uid = q("SELECT id FROM users WHERE username='admin'", one=True)['id']
    r = admin.post(f'/admin/users/{uid}/toggle', follow_redirects=True)
    assert '不可停用目前登入中的自己' in r.get_data(as_text=True)
    assert q('SELECT active FROM users WHERE id=?', (uid,), one=True)['active'] == 1


def test_second_admin_can_be_toggled():
    """再加一位管理員後，停用／啟用他都不應被誤擋（保護只鎖自己與最後一位）。"""
    admin = _new_client()
    assert _login(admin, 'admin', 'Pass@123').status_code == 302
    r = admin.post('/admin/users/add', data={'username': 'admin2', 'password': 'Pass@123',
                                             'name': '二號管理員', 'role': 'admin'})
    assert r.status_code == 302
    uid2 = q("SELECT id FROM users WHERE username='admin2'", one=True)['id']
    r = admin.post(f'/admin/users/{uid2}/toggle', follow_redirects=True)
    assert '已停用' in r.get_data(as_text=True)
    r = admin.post(f'/admin/users/{uid2}/toggle', follow_redirects=True)
    assert '已啟用' in r.get_data(as_text=True)


def test_last_admin_guard_logic():
    """toggle_user_error 的深度防禦分支：目標是最後一位在職管理員時攔截。"""
    with app.app_context():
        me = q("SELECT id, role, active FROM users WHERE username='admin'", one=True)
        # 直接以 SQL 備齊第三位管理員，並讓他成為唯一在職管理員
        execute("INSERT INTO users (username, password_hash, name, role) "
                "VALUES ('admin3', 'x', '三號管理員', 'admin')")
        uid3 = q("SELECT id FROM users WHERE username='admin3'", one=True)['id']
        # 讓 admin3 成為唯一在職管理員
        execute("UPDATE users SET active=0 WHERE role='admin' AND id<>?", (uid3,))
        target = q('SELECT id, username, role, active FROM users WHERE id=?', (uid3,), one=True)
        assert toggle_user_error(me, target) == '系統至少需保留一位在職管理員。'
        # 非管理員目標不受限
        stu = q("SELECT id, role, active FROM users WHERE username='2024004'", one=True)
        assert toggle_user_error(me, stu) is None
        # 清理，避免影響後續測試
        execute('DELETE FROM users WHERE username=?', ('admin3',))
        execute("UPDATE users SET active=1 WHERE role='admin'")


def test_add_user_rejects_invalid_role():
    admin = _new_client()
    _login(admin, 'admin', 'Pass@123')
    r = admin.post('/admin/users/add', data={'username': 'badrole', 'password': 'Pass@123',
                                             'name': '壞角色', 'role': 'superuser'},
                   follow_redirects=True)
    assert '角色不合法' in r.get_data(as_text=True)
    assert q("SELECT id FROM users WHERE username='badrole'", one=True) is None


def test_add_user_rejects_invalid_username():
    admin = _new_client()
    _login(admin, 'admin', 'Pass@123')
    r = admin.post('/admin/users/add', data={'username': 'a' * 51, 'password': 'Pass@123',
                                             'name': '超長帳號', 'role': 'student'},
                   follow_redirects=True)
    assert '帳號格式不合法' in r.get_data(as_text=True)
    r = admin.post('/admin/users/add', data={'username': 'has space', 'password': 'Pass@123',
                                             'name': '空白帳號', 'role': 'student'},
                   follow_redirects=True)
    assert '帳號格式不合法' in r.get_data(as_text=True)
    assert q('SELECT id FROM users WHERE username=?', ('a' * 51,), one=True) is None


# --------------------------------------------------------------------------
# 純函式（帳號格式 / 角色白名單）
# --------------------------------------------------------------------------
def test_valid_username():
    assert valid_username('2024001')
    assert valid_username('t.zhang-01')
    assert valid_username('a' * 50)
    assert not valid_username('a' * 51)
    assert not valid_username('')
    assert not valid_username('has space')
    assert not valid_username('中文帳號')
    assert not valid_username(None)


def test_valid_role():
    for r in ('student', 'advisor', 'faculty', 'admin'):
        assert valid_role(r)
    for r in ('', 'SuperUser', 'root', 'teacher'):
        assert not valid_role(r)
