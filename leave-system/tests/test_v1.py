# -*- coding: utf-8 -*-
"""v1.0.0 測試：儀表板統計範圍隔離、/approvals 角色下限、同日時段矛盾、
413 友善處理、超長附件檔名截斷、後台稽核日誌頁、撤回通知導師、
密碼長度上限、管理員重設密碼防重複。

沿用 conftest 的共用臨時資料庫與種子資料。為了不與其他測試檔搶帳號
（test_auth_hardening 會改動 2024001／2024002／2024005 的密碼），
本檔自建專用學生，登入與提交都走它們。
"""
import datetime
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute  # noqa: E402
from db import UPLOAD_DIR  # noqa: E402
from utils import validate_password, temp_password  # noqa: E402

TODAY = datetime.date.today()
PW = 'V1test@2026'
STU = 'v1_stu'            # 導師 T002 的學生（撤回通知測試用）
STU_A = 'v1_stu_a'        # 導師 T001 的學生（範圍隔離測試用）
STU_B = 'v1_stu_b'        # 導師 T002 的學生（範圍隔離測試用）


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def students():
    """自建三名學生：STU／STU_A 掛 T001、STU_B 掛 T002（已存在則沿用）。"""
    t1 = q("SELECT id FROM users WHERE username='T001'", one=True)['id']
    t2 = q("SELECT id FROM users WHERE username='T002'", one=True)['id']
    admin = app.test_client()
    admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
    for name, adv in ((STU, t2), (STU_A, t1), (STU_B, t2)):
        if not q('SELECT id FROM users WHERE username=?', (name,), one=True):
            r = admin.post('/admin/users/add', data={
                'username': name, 'password': PW, 'name': f'v1 測試生 {name}',
                'role': 'student', 'email': f'{name}@example.edu',
                'advisor_id': str(adv)})
            assert r.status_code == 200, f'建立 {name} 失敗'
    return {'t1': t1, 't2': t2,
            'stu': q('SELECT id FROM users WHERE username=?', (STU,), one=True)['id'],
            'a': q('SELECT id FROM users WHERE username=?', (STU_A,), one=True)['id'],
            'b': q('SELECT id FROM users WHERE username=?', (STU_B,), one=True)['id']}


def _login(client, username, password=PW):
    return client.post('/login', data={'username': username, 'password': password})


def _submit(client, day_offset, reason='v1 測試'):
    """以已登入學生身份提交一張一日假單，回傳 rid。"""
    d = TODAY + datetime.timedelta(days=day_offset)
    r = client.post('/new', data={'leave_type_id': '1',
                                  'start_date': d.isoformat(), 'end_date': d.isoformat(),
                                  'start_period': 'AM', 'end_period': 'PM',
                                  'reason': reason, 'contact_phone': '13800000001'},
                    content_type='multipart/form-data')
    assert r.status_code == 302, f'提交失敗：{r.status_code}'
    return int(r.headers['Location'].rsplit('/', 1)[-1])


# --------------------------------------------------------------------------
# 儀表板統計範圍隔離（v1.0.0）
# --------------------------------------------------------------------------
def test_dashboard_stats_scoped_for_advisor(students):
    """導師的儀表板彙總只算自己的學生——此前一律顯示全系統統計。"""
    c = app.test_client()
    _login(c, STU_A)
    _submit(c, 250, 'v1 範圍測試 A')
    c2 = app.test_client()
    _login(c2, STU_B)
    _submit(c2, 251, 'v1 範圍測試 B')

    expected = q("SELECT COUNT(*) c FROM leave_requests r JOIN users s ON s.id=r.student_id "
                 "WHERE s.advisor_id=? AND r.status='pending'",
                 (students['t1'],), one=True)['c']
    assert expected >= 1

    t1 = app.test_client()
    assert _login(t1, 'T001', 'Demo@2026').status_code == 302
    html = t1.get('/').get_data(as_text=True)
    m = re.search(r'<div class="n">(\d+)</div><div class="l">我的學生審批中</div>', html)
    assert m, '儀表板缺少範圍感知的統計标签'
    assert int(m.group(1)) == expected
    assert '全校審批中' not in html


def test_dashboard_stats_admin_sees_all(students):
    """管理員的儀表板仍是全校數字（角色範圍即全部）。"""
    c = app.test_client()
    _login(c, STU_A)
    _submit(c, 252, 'v1 管理員視角測試')
    expected = q("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'",
                 one=True)['c']

    admin = app.test_client()
    assert _login(admin, 'admin', 'Demo@2026').status_code == 302
    html = admin.get('/').get_data(as_text=True)
    m = re.search(r'<div class="n">(\d+)</div><div class="l">全校審批中</div>', html)
    assert m, '管理員儀表板缺少全校統計'
    assert int(m.group(1)) == expected


# --------------------------------------------------------------------------
# /approvals 角色下限（v1.0.0）：學生不再能翻全系統待審假單
# --------------------------------------------------------------------------
def test_approvals_requires_approver_role(students):
    c = app.test_client()
    assert _login(c, STU_A).status_code == 302
    r = c.get('/approvals')
    assert r.status_code == 302                      # 被導回 dashboard
    assert c.get('/approvals?scope=all').status_code == 302
    r = c.get('/approvals', follow_redirects=True)
    assert '沒有權限' in r.get_data(as_text=True)


def test_approvals_still_open_to_approvers(students):
    t1 = app.test_client()
    assert _login(t1, 'T001', 'Demo@2026').status_code == 302
    assert t1.get('/approvals').status_code == 200
    assert t1.get('/approvals?scope=all').status_code == 200
    f1 = app.test_client()
    assert _login(f1, 'F001', 'Demo@2026').status_code == 302
    assert f1.get('/approvals').status_code == 200


# --------------------------------------------------------------------------
# 同日「下午→上午」矛盾時段（v1.0.0）
# --------------------------------------------------------------------------
def test_same_day_pm_to_am_rejected(students):
    c = app.test_client()
    _login(c, STU_A)
    d = TODAY + datetime.timedelta(days=253)
    r = c.post('/new', data={'leave_type_id': '1',
                             'start_date': d.isoformat(), 'end_date': d.isoformat(),
                             'start_period': 'PM', 'end_period': 'AM',
                             'reason': '矛盾時段', 'contact_phone': '13800000001'},
               content_type='multipart/form-data')
    assert r.status_code == 200                      # 重渲染表單，不是 500
    assert '結束時段不可早於開始時段' in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) c FROM leave_requests WHERE reason='矛盾時段'",
             one=True)['c'] == 0
    # 同時段合法組合不受影響
    rid = _submit(c, 253, '同時段合法')
    assert rid > 0


# --------------------------------------------------------------------------
# 413 請求體過大（v1.0.0）：友善提示而不是白底錯誤頁
# --------------------------------------------------------------------------
def test_oversize_upload_friendly_413(students):
    c = app.test_client()
    _login(c, STU_A)
    old = app.config['MAX_CONTENT_LENGTH']
    app.config['MAX_CONTENT_LENGTH'] = 2048          # 暫時調低，模擬超過上限
    try:
        r = c.post('/new', data={'leave_type_id': '1',
                                 'start_date': (TODAY + datetime.timedelta(days=254)).isoformat(),
                                 'end_date': (TODAY + datetime.timedelta(days=254)).isoformat(),
                                 'reason': 'too big',
                                 'attachment': (io.BytesIO(b'%PDF-1.4\n' + b'x' * 4096),
                                                'big.pdf')},
                   content_type='multipart/form-data', follow_redirects=True)
        assert r.status_code == 200
        assert '過大' in r.get_data(as_text=True)
    finally:
        app.config['MAX_CONTENT_LENGTH'] = old
    assert q("SELECT COUNT(*) c FROM leave_requests WHERE reason='too big'",
             one=True)['c'] == 0


# --------------------------------------------------------------------------
# 超長附件檔名截斷（v1.0.0）
# --------------------------------------------------------------------------
def test_long_attachment_name_truncated(students):
    c = app.test_client()
    _login(c, STU_A)
    r = c.post('/new', data={'leave_type_id': '1',
                             'start_date': (TODAY + datetime.timedelta(days=255)).isoformat(),
                             'end_date': (TODAY + datetime.timedelta(days=255)).isoformat(),
                             'reason': '超長檔名', 'contact_phone': '13800000001',
                             'attachment': (io.BytesIO(b'%PDF-1.4\nlong name test'),
                                            'A' * 300 + '.pdf')},
               content_type='multipart/form-data')
    assert r.status_code == 302
    rid = int(r.headers['Location'].rsplit('/', 1)[-1])
    row = q('SELECT attachment FROM leave_requests WHERE id=?', (rid,), one=True)
    assert row['attachment']
    assert len(row['attachment']) <= 255             # 不超過檔案系統上限
    assert row['attachment'].endswith('.pdf')
    assert os.path.exists(os.path.join(UPLOAD_DIR, row['attachment']))


# --------------------------------------------------------------------------
# 後台稽核日誌頁（v1.0.0）
# --------------------------------------------------------------------------
def test_admin_logs_page(students):
    c = app.test_client()
    _login(c, STU_A)
    _submit(c, 256, 'v1 日誌測試')                   # 產生一筆 submit 紀錄
    admin = app.test_client()
    assert _login(admin, 'admin', 'Demo@2026').status_code == 302
    r = admin.get('/admin/logs')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '稽核日誌' in html
    assert '提交假單' in html                        # 動作標籤
    assert 'v1 日誌測試' not in html                 # 頁面只列操作，不擴寫假單內容
    # 動作篩選與越界頁碼
    assert admin.get('/admin/logs?action=submit').status_code == 200
    assert admin.get('/admin/logs?page=999').status_code == 200
    # 非管理員不可看
    s = app.test_client()
    _login(s, STU_A)
    assert s.get('/admin/logs').status_code == 302
    # 導師也不可看（僅管理員）
    t = app.test_client()
    _login(t, 'T001', 'Demo@2026')
    assert t.get('/admin/logs').status_code == 302


# --------------------------------------------------------------------------
# 撤回假單通知導師（v1.0.0）
# --------------------------------------------------------------------------
def test_withdraw_notifies_advisor(students):
    c = app.test_client()
    _login(c, STU)
    rid = _submit(c, 257, 'v1 撤回通知測試')
    r = c.post(f'/request/{rid}/withdraw')
    assert r.status_code == 302
    rows = q('SELECT message FROM notifications WHERE user_id=? AND request_id=?',
             (students['t2'], rid))
    assert any('撤回' in row['message'] for row in rows), '撤回後未通知導師'


# --------------------------------------------------------------------------
# 密碼長度上限（v1.0.0）
# --------------------------------------------------------------------------
def test_password_max_length():
    long_pw = 'Aa1' + 'x' * 200
    err = validate_password(long_pw)
    assert err is not None and '超過' in err
    # 邊界值：剛好 128 字元且其餘條件合格 → 通過
    ok_pw = 'Aa1' + 'x' * 125
    assert len(ok_pw) == 128
    assert validate_password(ok_pw) is None
    assert validate_password(temp_password()) is None


def test_admin_reset_same_password_rejected(students):
    """管理員把密碼重設成與目前相同：明確拒絕，且舊密碼仍然有效。"""
    admin = app.test_client()
    assert _login(admin, 'admin', 'Demo@2026').status_code == 302
    r = admin.post(f'/admin/users/{students["stu"]}/reset-password',
                   data={'password': PW}, follow_redirects=True)
    assert '不可與目前密碼相同' in r.get_data(as_text=True)
    c = app.test_client()
    assert _login(c, STU, PW).status_code == 302      # 舊密碼仍可登入
