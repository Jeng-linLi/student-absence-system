# -*- coding: utf-8 -*-
"""v1.9.0 測試：PII 讀取稽核（GAP-ANALYSIS D3 個資治理）。

守住一件事：跨範圍讀取個資的動作（統計頁、CSV 匯出、帳號清單）每次都必須
寫入 audit_logs，讓「誰在什麼時候看過／匯出了全範圍個資」可追溯到。

沿用 conftest 的共用臨時資料庫與種子帳號（admin / T001 導師 / F001 院系）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q  # noqa: E402
from utils import APP_VERSION  # noqa: E402

DEMO = 'Demo@2026'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


def _login(c, username, password=DEMO):
    return c.post('/login', data={'username': username, 'password': password})


def _uid(username):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _count(action, actor_id):
    return q('SELECT COUNT(*) c FROM audit_logs WHERE action=? AND actor_id=?',
             (action, actor_id), one=True)['c']


# --------------------------------------------------------------------------
# 版本
# --------------------------------------------------------------------------
def test_version_is_1_9_0():
    assert APP_VERSION == '1.9.0'


# --------------------------------------------------------------------------
# D3：PII 讀取稽核
# --------------------------------------------------------------------------
def test_stats_logs_view_pii():
    c = app.test_client()
    _login(c, 'T001')  # 導師
    uid = _uid('T001')
    before = _count('view_pii', uid)
    r = c.get('/stats')
    assert r.status_code == 200
    # 統計頁跨範圍讀取學生 PII，必須新增一筆 view_pii 稽核
    assert _count('view_pii', uid) == before + 1


def test_export_logs_export():
    c = app.test_client()
    _login(c, 'T001')  # 導師
    uid = _uid('T001')
    before = _count('export', uid)
    r = c.get('/export')
    # 串流 CSV 含事由等敏感欄位，回傳前即寫入 export 稽核
    assert r.status_code == 200
    assert _count('export', uid) == before + 1


def test_admin_users_logs_view_pii():
    c = app.test_client()
    _login(c, 'admin')  # 管理員
    uid = _uid('admin')
    before = _count('view_pii', uid)
    r = c.get('/admin/users')
    assert r.status_code == 200
    # 帳號清單列出全體帳號 email 等 PII，必須新增一筆 view_pii 稽核
    assert _count('view_pii', uid) == before + 1


def test_admin_logs_filter_shows_new_actions():
    """後台稽核頁的動作篩選下拉應包含 export / view_pii 標籤。"""
    from app import AUDIT_ACTION_LABEL
    assert AUDIT_ACTION_LABEL.get('export') == '匯出資料'
    assert AUDIT_ACTION_LABEL.get('view_pii') == '檢視個資'
