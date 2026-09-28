# -*- coding: utf-8 -*-
"""冒煙測試：走完整鏈路 提交 → 導師核准 → 院系核准 → 生效"""
import os, sys, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import app, calc_days, init_db, seed

with app.app_context():
    init_db(); seed()

c = app.test_client()


def login(u):
    r = c.post('/login', data={'username': u, 'password': 'Pass@123'},
               follow_redirects=False)
    assert r.status_code == 302, f'login failed for {u}: {r.status_code}'
    return r


def get(path):
    r = c.get(path)
    assert r.status_code == 200, f'GET {path} -> {r.status_code}'
    return r.get_data(as_text=True)


print('1) 天數計算：', calc_days(__import__('datetime').date(2026, 9, 23),
                                 __import__('datetime').date(2026, 9, 23), 'AM', 'PM'), '(應為1.0)')
print('   半天：', calc_days(__import__('datetime').date(2026, 9, 23),
                             __import__('datetime').date(2026, 9, 23), 'AM', 'AM'), '(應為0.5)')
print('   跨5天：', calc_days(__import__('datetime').date(2026, 9, 21),
                              __import__('datetime').date(2026, 9, 25), 'AM', 'PM'), '(應為5.0)')

# --- 學生提交 6 天病假（應走兩級）---
login('2024001')
get('/')
html = get('/new')
assert '病假' in html
import datetime
sd = datetime.date.today()
ed = sd + datetime.timedelta(days=5)
data = {'leave_type_id': '2', 'start_date': sd.isoformat(), 'end_date': ed.isoformat(),
        'start_period': 'AM', 'end_period': 'PM', 'reason': '住院治療',
        'contact_phone': '13800000001',
        'attachment': (io.BytesIO(b'fake-proof'), 'proof.pdf')}
r = c.post('/new', data=data, content_type='multipart/form-data')
assert r.status_code == 302, r.status_code
rid = int(r.headers['Location'].rsplit('/', 1)[-1])
print(f'2) 學生提交假單 #{rid} → 302 OK')

d = get(f'/request/{rid}')
assert '待導師審批' in d, '狀態應為待導師審批'
print('3) 提交後狀態：待導師審批 ✓')

# --- 導師核准 → 應轉院系 ---
login('T001')
a = get('/approvals')
assert str(rid) in a, '導師待辦清單未出現該單'
r = c.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '同意'})
assert r.status_code == 302
d = get(f'/request/{rid}')
assert '待院系審批' in d, '導師核准後應轉院系'
print('4) 導師核准 → 狀態轉「待院系審批」✓')

# --- 院系核准 → 生效 ---
login('F001')
a = get('/approvals')
assert str(rid) in a, '院系待辦清單未出現該單'
c.post(f'/request/{rid}/decide', data={'action': 'approve', 'comment': '核備'})
d = get(f'/request/{rid}')
assert '已核准' in d, '院系核准後應為已核准'
print('5) 院系核准 → 狀態「已核准」✓')

# --- 短假單：導師核准即生效 ---
login('2024003')
sd2 = datetime.date.today()
data = {'leave_type_id': '1', 'start_date': sd2.isoformat(), 'end_date': sd2.isoformat(),
        'start_period': 'AM', 'end_period': 'PM', 'reason': '家中事務'}
r = c.post('/new', data=data, content_type='multipart/form-data')
rid2 = int(r.headers['Location'].rsplit('/', 1)[-1])
login('T001')
c.post(f'/request/{rid2}/decide', data={'action': 'approve', 'comment': 'OK'})
d = get(f'/request/{rid2}')
assert '已核准' in d, '1天事假導師核准後應直接生效'
print('6) 1 天事假導師核准即生效 ✓')

# --- 駁回 ---
login('2024004')
data = {'leave_type_id': '1', 'start_date': sd2.isoformat(), 'end_date': sd2.isoformat(),
        'start_period': 'AM', 'end_period': 'PM', 'reason': '外出'}
r = c.post('/new', data=data, content_type='multipart/form-data')
rid3 = int(r.headers['Location'].rsplit('/', 1)[-1])
login('T002')
c.post(f'/request/{rid3}/decide', data={'action': 'reject', 'comment': '事由不清'})
d = get(f'/request/{rid3}')
assert '已駁回' in d
print('7) 駁回含理由 → 狀態「已駁回」✓')

# --- 撤回 ---
login('2024005')
data = {'leave_type_id': '1', 'start_date': sd2.isoformat(), 'end_date': sd2.isoformat(),
        'start_period': 'AM', 'end_period': 'PM', 'reason': '事假'}
r = c.post('/new', data=data, content_type='multipart/form-data')
rid4 = int(r.headers['Location'].rsplit('/', 1)[-1])
c.post(f'/request/{rid4}/withdraw')
d = get(f'/request/{rid4}')
assert '已撤回' in d
print('8) 學生撤回 → 狀態「已撤回」✓')

# --- 權限：學生不能審批 ---
login('2024001')
r = c.post(f'/request/{rid2}/decide', data={'action': 'approve', 'comment': 'x'},
           follow_redirects=True)
assert b'\xe4\xbd\xa0\xe6\xb2\x92\xe6\x9c\x89\xe5\xaf\xa9\xe6\x89\xb9\xe6\xac\x8a\xe9\x99\x90' in r.get_data() \
    or '沒有' in r.get_data(as_text=True)
print('9) 學生嘗試審批被拒 ✓')

# --- 其他頁面 ---
for u, paths in [('T001', ['/stats', '/export', '/approvals?scope=all', '/notifications']),
                 ('admin', ['/admin/users', '/admin/types']),
                 ('2024001', ['/my', '/my?status=approved'])]:
    login(u)
    for p in paths:
        r = c.get(p)
        assert r.status_code == 200, f'{u} GET {p} -> {r.status_code}'
print('10) 統計/匯出/後台/我的假單 頁面全部 200 ✓')

print('\nALL SMOKE TESTS PASSED')
