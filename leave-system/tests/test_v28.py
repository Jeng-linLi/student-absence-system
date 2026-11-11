# -*- coding: utf-8 -*-
"""v1.19.2：密碼重設 token 的兌換守衛失敗不得回報成功。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from app import app, execute, q  # noqa: E402


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


def test_reset_guard_failure_is_not_reported_as_success(monkeypatch):
    """模擬併發請求已先兌換：後到的請求只能失敗，且不可留下任何寫入。"""
    username = 'v28_reset_guard'
    if not q('SELECT id FROM users WHERE username=?', (username,), one=True):
        execute(
            'INSERT INTO users (username, password_hash, name, role, department, email) '
            'VALUES (?,?,?,?,?,?)',
            (username, generate_password_hash('V28old@2026'), '重設守衛測試生',
             'student', '人工智能學域', f'{username}@example.edu'))
    user = q('SELECT * FROM users WHERE username=?', (username,), one=True)
    before = dict(user)
    raw = app_module.issue_token(user['id'], 'reset')
    assert raw
    before_logs = q("SELECT COUNT(*) c FROM audit_logs WHERE action='reset_password'",
                    one=True)['c']

    # consume_token() 的查詢與 UPDATE 之間存在併發窗口；直接模擬守衛影響 0 列。
    monkeypatch.setattr(app_module, 'execute_guard', lambda *args, **kwargs: 0)
    client = app.test_client()
    response = client.post(
        f'/reset/{raw}',
        data={'new_password': 'V28new@2026', 'confirm_password': 'V28new@2026'},
        follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.request.path == '/forgot'
    assert '重設連結已失效' in page
    assert '密碼已重設' not in page
    assert dict(q('SELECT * FROM users WHERE id=?', (user['id'],), one=True)) == before
    assert q('SELECT used_at FROM password_resets WHERE token_hash=?',
             (app_module._hash_token(raw),), one=True)['used_at'] is None
    assert q("SELECT COUNT(*) c FROM audit_logs WHERE action='reset_password'",
             one=True)['c'] == before_logs
