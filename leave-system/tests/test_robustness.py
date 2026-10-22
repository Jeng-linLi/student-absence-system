# -*- coding: utf-8 -*-
"""v0.9.0 健壯性測試：通知分頁、附件內容嗅探、快取標頭、
狀態篩選白名單、Email／導師欄位校驗。

沿用 conftest 的共用臨時資料庫與種子資料。為了不與其他測試檔搶帳號
（test_auth_hardening 會改動 2024001／2024002／2024005 的密碼），
本檔自建一個專用學生 robust_stu，所有登入都走它。
"""
import datetime
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, _parse_advisor_id  # noqa: E402
from utils import sniff_allowed_type, valid_email, LEAVE_STATUSES  # noqa: E402

STU = 'robust_stu'
PW = 'Robust@2026'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def stu():
    """專用學生帳號（已存在則沿用）。"""
    if not q('SELECT id FROM users WHERE username=?', (STU,), one=True):
        admin = app.test_client()
        admin.post('/login', data={'username': 'admin', 'password': 'Demo@2026'})
        admin.post('/admin/users/add', data={'username': STU, 'password': PW,
                                             'name': '健壯性測試生', 'role': 'student',
                                             'email': f'{STU}@example.edu'})
    return q('SELECT id FROM users WHERE username=?', (STU,), one=True)['id']


def _login(client, username=STU, password=PW):
    return client.post('/login', data={'username': username, 'password': password})


# --------------------------------------------------------------------------
# 純函式：魔術位元組嗅探 / Email 校驗
# --------------------------------------------------------------------------
def test_sniff_allowed_type():
    assert sniff_allowed_type(b'%PDF-1.7\n...')
    assert sniff_allowed_type(b'\x89PNG\r\n\x1a\n')
    assert sniff_allowed_type(b'\xff\xd8\xff\xe0JFIF')
    assert sniff_allowed_type(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')   # OLE2 → .doc
    assert sniff_allowed_type(b'PK\x03\x04\x14\x00\x06\x00')          # ZIP → .docx
    # 副檔名說謊：內容頭對不上
    assert not sniff_allowed_type(b'MZ\x90\x00...')                   # 可執行檔
    assert not sniff_allowed_type(b'fake-proof')
    assert not sniff_allowed_type(b'')
    assert not sniff_allowed_type(b'plain text here')
    # 位元組數不足但前綴命中仍算通過（startswith 語義）
    assert sniff_allowed_type(b'%PDF')


def test_valid_email():
    assert valid_email('2024001@example.edu')
    assert valid_email('t.zhang-01@a.b.c')
    assert not valid_email('')
    assert not valid_email('no-at-sign')
    assert not valid_email('a@b')                    # 沒有頂級域
    assert not valid_email('a b@c.com')              # 含空白
    assert not valid_email(None)


def test_leave_statuses_match_schema():
    """白名單須與 schema.sql 的 CHECK 約束一致（四種狀態）。"""
    assert set(LEAVE_STATUSES) == {'pending', 'approved', 'rejected', 'withdrawn'}


# --------------------------------------------------------------------------
# 通知分頁與未讀篩選
# --------------------------------------------------------------------------
def _make_notifications(user_id, n, unread_first=True):
    """寫入 n 筆通知；前 3 筆未讀。"""
    for i in range(n):
        is_read = 0 if (unread_first and i < 3) else 1
        execute('INSERT INTO notifications (user_id, message, is_read, created_at) '
                'VALUES (?,?,?,?)',
                (user_id, f'v09 測試通知 {i}', is_read,
                 f'2026-10-2{1 + i % 9} 10:00:00'))


def test_notifications_pagination(stu):
    c = app.test_client()
    _login(c)
    before = q('SELECT COUNT(*) c FROM notifications WHERE user_id=?', (stu,), one=True)['c']
    _make_notifications(stu, 25)
    total = before + 25
    total_pages = max((total + 19) // 20, 1)

    r = c.get('/notifications')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '下一頁' in html                       # 超過一頁 → 分頁元件浮現
    assert f'共 {total} 筆' in html
    assert f'第 1 / {total_pages} 頁' in html

    assert c.get('/notifications?page=2').status_code == 200
    # 超出範圍的頁碼被收斂到最後一頁，不會 404／500
    assert c.get('/notifications?page=999').status_code == 200


def test_notifications_unread_filter(stu):
    c = app.test_client()
    _login(c)
    _make_notifications(stu, 6)          # 前 3 筆未讀
    r = c.get('/notifications?filter=unread')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'v09 測試通知 0' in html and 'v09 測試通知 2' in html
    assert 'v09 測試通知 5' not in html       # 已讀的被濾掉
    # 全部篩選則兩者都在
    html_all = c.get('/notifications').get_data(as_text=True)
    assert 'v09 測試通知 5' in html_all


def test_notifications_invalid_filter_falls_back(stu):
    c = app.test_client()
    _login(c)
    r = c.get('/notifications?filter=bogus')
    assert r.status_code == 200           # 非法值回落為「全部」而非報錯


# --------------------------------------------------------------------------
# 附件魔術位元組嗅探（路由層）
# --------------------------------------------------------------------------
def _fut(n=80):
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


def test_upload_with_fake_pdf_header_rejected(stu):
    c = app.test_client()
    _login(c)
    before = q('SELECT COUNT(*) c FROM leave_requests', one=True)['c']
    r = c.post('/new', data={'leave_type_id': '1',
                             'start_date': _fut(), 'end_date': _fut(),
                             'reason': '假 PDF', 'contact_phone': '13800000001',
                             'attachment': (io.BytesIO(b'MZ\x90\x00 not a pdf'), 'evil.pdf')},
               content_type='multipart/form-data')
    assert r.status_code == 200
    assert '格式不符' in r.get_data(as_text=True)
    assert q('SELECT COUNT(*) c FROM leave_requests', one=True)['c'] == before


def test_upload_with_real_pdf_header_accepted(stu):
    c = app.test_client()
    _login(c)
    r = c.post('/new', data={'leave_type_id': '1',
                             'start_date': _fut(81), 'end_date': _fut(81),
                             'reason': '真 PDF', 'contact_phone': '13800000001',
                             'attachment': (io.BytesIO(b'%PDF-1.4\n% fake'), 'ok.pdf')},
               content_type='multipart/form-data')
    assert r.status_code == 302          # 成功 → 重導向到假單詳情


# --------------------------------------------------------------------------
# 快取標頭：動態頁面 no-store、靜態資源不設
# --------------------------------------------------------------------------
def test_dynamic_pages_no_store(stu):
    c = app.test_client()
    r = c.get('/login')
    assert r.headers.get('Cache-Control') == 'no-store'
    _login(c)
    assert c.get('/').headers.get('Cache-Control') == 'no-store'


def test_static_files_not_no_store():
    c = app.test_client()
    r = c.get('/static/css/style.css')
    assert r.status_code == 200
    assert 'no-store' not in (r.headers.get('Cache-Control') or '')


# --------------------------------------------------------------------------
# 「我的假單」狀態篩選白名單
# --------------------------------------------------------------------------
def test_my_requests_invalid_status_ignored(stu):
    c = app.test_client()
    _login(c)
    r = c.get('/my?status=deleted-or-what')
    assert r.status_code == 200           # 非法狀態 → 視為不篩選，不報錯


# --------------------------------------------------------------------------
# 後台欄位校驗：Email 與導師歸屬
# --------------------------------------------------------------------------
def _admin():
    c = app.test_client()
    assert _login(c, 'admin', 'Demo@2026').status_code == 302
    return c


def test_add_user_rejects_bad_email():
    admin = _admin()
    r = admin.post('/admin/users/add', data={'username': 'bademail_u', 'password': 'GoodPass9',
                                             'name': '壞郵箱', 'role': 'student',
                                             'email': 'not-an-email'},
                   follow_redirects=True)
    assert 'Email' in r.get_data(as_text=True)
    assert q("SELECT id FROM users WHERE username='bademail_u'", one=True) is None


def test_add_user_rejects_unknown_advisor():
    admin = _admin()
    r = admin.post('/admin/users/add', data={'username': 'badvadv_u', 'password': 'GoodPass9',
                                             'name': '壞導師', 'role': 'student',
                                             'advisor_id': '99999'},
                   follow_redirects=True)
    assert '導師' in r.get_data(as_text=True)
    assert q("SELECT id FROM users WHERE username='badvadv_u'", one=True) is None


def test_parse_advisor_id():
    adv = q("SELECT id FROM users WHERE role='advisor' AND active=1", one=True)
    stu = q("SELECT id FROM users WHERE role='student'", one=True)
    assert _parse_advisor_id('') == (None, None)
    assert _parse_advisor_id(None) == (None, None)
    assert _parse_advisor_id(str(adv['id'])) == (adv['id'], None)
    assert _parse_advisor_id('abc')[1] is not None
    assert _parse_advisor_id('99999')[1] is not None
    # 學生 id 不是導師 → 擋下
    assert _parse_advisor_id(str(stu['id']))[1] is not None
