# -*- coding: utf-8 -*-
"""v1.7.0 測試：課表真實化（GAP-ANALYSIS C1）。

v1.6.0 以前所有學生共用同一份 DEMO_TIMETABLE，因此「課表感知受影響課程」
對任何一個真實學生都可能是錯的。本檔守住三件事：

    1. CSV 解析是寬鬆的（中英簡繁欄名、可省略表頭、含逗號的教室），
       但不能因為寬鬆而收下錯的資料：週末、結束早於開始、空白學號／
       課程代碼都要進 errors，且不得拖累同一份檔案裡的正確列。
    2. 時間必須以零填充的 HH:MM 存庫。'9:00' < '12:00' 是 False，
       不補零的話上午的課會被判成下午的課。
    3. 課表是逐生的：匯入後兩個學生的受影響課程必須不同——這正是
       v1.6.0 做不到的那件事。

沿用 conftest 的共用臨時資料庫；自建 v17_* 帳號避免與其他測試檔搶種子資料。
本檔字母序在 test_v16 之後，最後執行。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, affected_courses_for  # noqa: E402
from utils import APP_VERSION  # noqa: E402
from timetable_import import (parse_timetable_csv, apply_timetable,  # noqa: E402
                              timetable_coverage, pad_time, MAX_IMPORT_ROWS)

TODAY = datetime.date.today()
PW = 'V17test@2026'
DEMO = 'Demo@2026'
STU_A = 'v17_a'      # 導師 T001 的學生
STU_B = 'v17_b'      # 導師 T002 的學生（越權測試）
DEPT_AI = '人工智能學域'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def admin_client():
    c = app.test_client()
    assert c.post('/login', data={'username': 'admin', 'password': DEMO}).status_code == 302
    return c


def _uid(username):
    return q('SELECT id FROM users WHERE username=?', (username,), one=True)['id']


def _login(username, password=PW):
    c = app.test_client()
    assert c.post('/login', data={'username': username, 'password': password}).status_code == 302
    return c


@pytest.fixture()
def setup_students(admin_client):
    """建立 v17 專用學生（不帶課表），並清掉既有的課表列以確保可重複執行。"""
    execute('DELETE FROM delegations')
    for username, name, adv in ((STU_A, 'v17 甲生', 'T001'), (STU_B, 'v17 乙生', 'T002')):
        if not q('SELECT id FROM users WHERE username=?', (username,), one=True):
            assert admin_client.post('/admin/users/add', data={
                'username': username, 'password': PW, 'name': name, 'role': 'student',
                'department': DEPT_AI, 'advisor_id': str(_uid(adv)),
                'email': f'{username}@example.edu'}).status_code == 200
    for username in (STU_A, STU_B):
        execute('DELETE FROM timetable WHERE student_id=?', (_uid(username),))
    return _uid(STU_A)


def _clear(uid):
    """清掉 v17 測試寫入的課表列（每個用到課表的測試結束時還原）。"""
    execute('DELETE FROM timetable WHERE student_id=?', (uid,))


CSV_A = """# 學號,星期,開始時間,結束時間,課程代碼,類型,教室,備註
v17_a,mon,09:00,10:20,V17-A-AM,Lecture,"Rm 102, Building A",W4
v17_a,mon,14:00,15:20,V17-A-PM,Lab,Rm 201
v17_a,wed,10:30,12:00,V17-A-WED,Seminar,Rm 305
"""


# --------------------------------------------------------------------------
# 版本號
# --------------------------------------------------------------------------
def test_app_version_bumped():
    """v1.7.0：版本號已提升。"""
    assert tuple(map(int, APP_VERSION.split('.'))) >= (1, 7, 0)


# --------------------------------------------------------------------------
# CSV 解析
# --------------------------------------------------------------------------
def test_parse_csv_with_header_and_comments():
    """有表頭、有註解、教室含逗號（雙引號包住）都解析正確。"""
    p = parse_timetable_csv(CSV_A)
    assert not p['errors'], p['errors']
    assert len(p['rows']) == 3
    first = p['rows'][0]
    assert first['weekday'] == 0 and first['start_time'] == '09:00'
    assert first['end_time'] == '10:20' and first['course_code'] == 'V17-A-AM'
    assert first['room'] == 'Rm 102, Building A', '含逗號的教室必須完整保留'
    assert first['note'] == 'W4'


def test_parse_csv_without_header_is_positional():
    """省略表頭時按 student_no,weekday,start,end,code,... 順序解讀。"""
    p = parse_timetable_csv('v17_a,tue,08:00,09:40,V17-NEW')
    assert not p['errors'], p['errors']
    assert len(p['rows']) == 1
    assert p['rows'][0]['weekday'] == 1 and p['rows'][0]['course_code'] == 'V17-NEW'


def test_parse_csv_accepts_simplified_and_english_headers():
    """簡體與英文欄名都要認：教務處匯出的檔不該要求對方改名。"""
    p = parse_timetable_csv('学号,星期,开始时间,结束时间,课程代码\n'
                            'v17_a,3,13:00,14:40,V17-ZH')
    assert not p['errors'], p['errors']
    assert p['rows'][0]['weekday'] == 2 and p['rows'][0]['course_code'] == 'V17-ZH'

    p2 = parse_timetable_csv('student_no,weekday,start_time,end_time,course\n'
                             'v17_a,fri,15:00,16:40,V17-EN')
    assert not p2['errors'], p2['errors']
    assert p2['rows'][0]['weekday'] == 4


def test_parse_csv_pads_time():
    """'9:00' 必須補成 '09:00'——否則字串比較會把上午課判成下午課。"""
    p = parse_timetable_csv('v17_a,mon,9:00,9:50,V17-PAD')
    assert p['rows'][0]['start_time'] == '09:00'
    assert p['rows'][0]['end_time'] == '09:50'
    assert pad_time('9:00') == '09:00'
    assert pad_time('9:5') == '09:05'
    assert pad_time('不是時間') == '不是時間'      # 格式不合時原樣回傳


def test_parse_csv_bad_rows_do_not_stop_the_rest():
    """錯的列進 errors，對的列照收：管理員改一行就好，不必重貼整份。"""
    p = parse_timetable_csv(
        'v17_a,mon,09:00,10:20,V17-OK\n'
        'v17_a,sat,09:00,10:20,V17-WEEKEND\n'      # 週末
        'v17_a,mon,10:20,09:00,V17-REVERSED\n'     # 結束早於開始
        'v17_a,mon,25:00,26:00,V17-OUTOFRANGE\n'   # 時間超範圍
        ',mon,09:00,10:20,V17-NOSTU\n'             # 學號空白
        'v17_a,mon,09:00,10:20,\n'                 # 課程代碼空白
        'v17_a,星期八,09:00,10:20,V17-BADWD\n'      # 認不出星期
        'v17_a,tue,08:00,09:40,V17-STILL-OK\n')
    codes = [r['course_code'] for r in p['rows']]
    assert codes == ['V17-OK', 'V17-STILL-OK'], codes
    assert len(p['errors']) == 6, [e['msg'] for e in p['errors']]
    assert any('週末' in e['msg'] for e in p['errors'])
    # 錯誤要帶行號，管理員才知道改哪一行
    assert {e['line'] for e in p['errors']} == {2, 3, 4, 5, 6, 7}


def test_parse_csv_row_limit():
    """超過單次上限整批拒收，避免貼錯檔時寫爆資料庫。"""
    body = '\n'.join(f'v17_a,mon,09:00,10:20,V17-{i}' for i in range(MAX_IMPORT_ROWS + 1))
    p = parse_timetable_csv(body)
    assert p['rows'] == []
    assert len(p['errors']) == 1 and '上限' in p['errors'][0]['msg']


def test_parse_csv_empty_text():
    """空白／純註解輸入不該噴錯，只是沒有列。"""
    assert parse_timetable_csv('')['rows'] == []
    assert parse_timetable_csv('# 只有註解\n\n\n')['rows'] == []


# --------------------------------------------------------------------------
# 寫入資料庫
# --------------------------------------------------------------------------
def test_apply_replace_clears_that_student_first(setup_students):
    """replace：清空「檔案中出現的學生」的舊課表再寫入（對應整份匯出）。"""
    uid = setup_students
    execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
            "course_code, source) VALUES (?,0,'08:00','09:00','OLD-KEEP','manual')", (uid,))
    parsed = parse_timetable_csv(CSV_A)
    stats = apply_timetable(parsed, mode='replace')
    assert stats['students'] == 1 and stats['rows'] == 3
    assert stats['deleted'] == 1, '舊課表應先被清除'
    assert stats['unknown'] == []
    codes = [r['course_code'] for r in q(
        'SELECT course_code FROM timetable WHERE student_id=?', (uid,))]
    assert codes == ['V17-A-AM', 'V17-A-PM', 'V17-A-WED'], codes
    _clear(uid)


def test_apply_merge_keeps_existing_rows(setup_students):
    """merge：只新增／更新檔案裡的課，其餘維持不動（增量補課用）。"""
    uid = setup_students
    apply_timetable(parse_timetable_csv(CSV_A), mode='merge')
    before = q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,), one=True)['c']
    apply_timetable(parse_timetable_csv(CSV_A), mode='merge')
    after = q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,), one=True)['c']
    assert before == after == 3, '重複匯入同一份檔不得長出重複的課（冪等）'
    _clear(uid)


def test_apply_unknown_student_no_is_skipped(setup_students):
    """學號查無帳號只略過該列：匯一份全年級課表時，有休學學號是常態。"""
    uid = setup_students
    parsed = parse_timetable_csv('v17_a,mon,09:00,10:20,V17-A-AM\n'
                                 'v17_ghost,mon,09:00,10:20,GHOST\n')
    stats = apply_timetable(parsed, mode='merge')
    assert stats['rows'] == 1 and stats['students'] == 1
    assert stats['unknown'] == ['v17_ghost']
    assert q('SELECT COUNT(*) c FROM timetable WHERE course_code=?', ('GHOST',),
             one=True)['c'] == 0
    _clear(uid)


def test_apply_rejects_unknown_mode(setup_students):
    """mode 不是 replace/merge 時直接擋下，且一列都不寫入。"""
    uid = setup_students
    with pytest.raises(ValueError):
        apply_timetable(parse_timetable_csv(CSV_A), mode='upsert')
    assert q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,),
             one=True)['c'] == 0
    _clear(uid)


def test_manual_rows_are_not_demo(setup_students):
    """來源標記決定覆蓋率統計：手動或匯入的課不算示範資料。"""
    uid = setup_students
    before = timetable_coverage()['demo_only']
    execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
            "course_code, source) VALUES (?,0,'08:00','09:00','MANUAL-1','manual')", (uid,))
    cov = timetable_coverage()
    assert cov['demo_only'] == before, '有非示範課表的學生不該計入 demo_only'
    assert cov['imported'] >= 1
    assert cov['students'] >= 5 and cov['with_tt'] >= 5
    assert cov['missing'] == max(cov['students'] - cov['with_tt'], 0)
    _clear(uid)


# --------------------------------------------------------------------------
# 課表感知：不同學生的受影響課程必須不同（v1.6.0 做不到的那件事）
# --------------------------------------------------------------------------
def _next_weekday_date(target_wd, min_offset=800):
    """找一個未來的上課日（非週末、不在校曆假日）且星期為 target_wd。"""
    off = {r['day'] for r in q('SELECT day FROM holidays')}
    d = TODAY + datetime.timedelta(days=min_offset)
    for _ in range(60):
        if d.weekday() == target_wd and d.isoformat() not in off:
            return d
        d += datetime.timedelta(days=1)
    raise AssertionError('找不到合適的上課日')


def test_affected_courses_differ_per_student(setup_students):
    """同一天請假，兩個學生的受影響課程不同——課表真實化的核心。"""
    uid_a, uid_b = _uid(STU_A), _uid(STU_B)
    apply_timetable(parse_timetable_csv(
        'v17_a,mon,09:00,10:20,V17-A-AM\n'
        'v17_b,mon,09:00,10:20,V17-B-AM\n'), mode='replace')
    monday = _next_weekday_date(0)

    a = [c['course_code'] for d in affected_courses_for(uid_a, monday.isoformat(),
                                                        monday.isoformat())
         for c in d['courses']]
    b = [c['course_code'] for d in affected_courses_for(uid_b, monday.isoformat(),
                                                        monday.isoformat())
         for c in d['courses']]
    assert a == ['V17-A-AM'], a
    assert b == ['V17-B-AM'], b
    assert a != b, '課表若仍共用一份，這個測試不可能通過'
    _clear(uid_a)
    _clear(uid_b)


def test_unpadded_time_would_misclassify_am_course(setup_students):
    """上午的課必須算進上午半天：這是 pad_time 存在的理由（回歸保護）。"""
    uid = setup_students
    apply_timetable(parse_timetable_csv('v17_a,mon,9:00,9:50,V17-EARLY'), mode='replace')
    monday = _next_weekday_date(0)
    am = affected_courses_for(uid, monday.isoformat(), monday.isoformat(),
                              'AM', 'AM')
    pm = affected_courses_for(uid, monday.isoformat(), monday.isoformat(),
                              'PM', 'PM')
    assert [c['course_code'] for d in am for c in d['courses']] == ['V17-EARLY']
    assert pm == [], '9:00 的課不該被算成下午'
    _clear(uid)


# --------------------------------------------------------------------------
# 管理端點
# --------------------------------------------------------------------------
def test_admin_import_endpoint(admin_client, setup_students):
    """管理頁匯入：寫入、回報、留稽核軌跡。"""
    uid = setup_students
    html = admin_client.post('/admin/timetable/import',
                             data={'csv_text': CSV_A, 'mode': 'replace'},
                             follow_redirects=True).get_data(as_text=True)
    assert '已匯入 3 節課' in html, html[:400]
    assert q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,),
             one=True)['c'] == 3
    logs = q("SELECT detail FROM audit_logs WHERE action='import_timetable'")
    assert any('3 節課' in r['detail'] for r in logs), '匯入未留稽核軌跡'
    _clear(uid)


def test_admin_import_reports_unknown_and_bad_rows(admin_client, setup_students):
    """匯入結果要講清楚：幾個學號查無此人、幾列格式有誤。"""
    uid = setup_students
    html = admin_client.post('/admin/timetable/import', data={
        'csv_text': 'v17_a,mon,09:00,10:20,V17-A-AM\n'
                    'v17_ghost,mon,09:00,10:20,GHOST\n'
                    'v17_a,sat,09:00,10:20,WEEKEND\n',
        'mode': 'merge'}, follow_redirects=True).get_data(as_text=True)
    assert '查無帳號已略過' in html and '格式有誤已略過' in html
    _clear(uid)


def test_admin_import_rejects_nothing_parsed(admin_client):
    html = admin_client.post('/admin/timetable/import', data={'csv_text': '# 空檔'},
                             follow_redirects=True).get_data(as_text=True)
    assert '沒有解析到任何課' in html


def test_admin_import_requires_admin(setup_students):
    """導師不能匯入課表。"""
    uid = setup_students
    t = _login('T001', DEMO)
    r = t.post('/admin/timetable/import', data={'csv_text': CSV_A})
    assert r.status_code == 302
    assert q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,),
             one=True)['c'] == 0
    _clear(uid)


def test_admin_add_row_pads_time_and_validates(admin_client, setup_students):
    """單筆新增：時間補零、結束需晚於開始、課程代碼必填。"""
    uid = setup_students
    ok = admin_client.post('/admin/timetable/add', data={
        'uid': uid, 'weekday': 0, 'start_time': '9:00', 'end_time': '10:20',
        'course_code': 'V17-MANUAL', 'kind': 'Lab', 'room': 'Rm 1'},
        follow_redirects=True).get_data(as_text=True)
    assert '已加入' in ok
    row = q('SELECT start_time, end_time, source FROM timetable WHERE student_id=?',
            (uid,), one=True)
    assert row['start_time'] == '09:00', '手動新增也必須補零'
    assert row['source'] == 'manual'

    bad = admin_client.post('/admin/timetable/add', data={
        'uid': uid, 'weekday': 0, 'start_time': '10:00', 'end_time': '09:00',
        'course_code': 'X'}, follow_redirects=True).get_data(as_text=True)
    assert '結束時間需晚於開始時間' in bad
    bad2 = admin_client.post('/admin/timetable/add', data={
        'uid': uid, 'weekday': 0, 'start_time': '25:00', 'end_time': '26:00',
        'course_code': 'X'}, follow_redirects=True).get_data(as_text=True)
    assert '時間格式需為' in bad2
    assert q('SELECT COUNT(*) c FROM timetable WHERE student_id=?', (uid,),
             one=True)['c'] == 1, '驗證失敗的資料不得寫入'
    _clear(uid)


def test_admin_delete_row(admin_client, setup_students):
    """刪除一節課並留稽核軌跡。"""
    uid = setup_students
    apply_timetable(parse_timetable_csv(CSV_A), mode='replace')
    tid = q('SELECT id FROM timetable WHERE student_id=? AND course_code=?',
            (uid, 'V17-A-AM'), one=True)['id']
    html = admin_client.post(f'/admin/timetable/{tid}/delete',
                             follow_redirects=True).get_data(as_text=True)
    assert '已移除' in html
    assert q('SELECT 1 FROM timetable WHERE id=?', (tid,), one=True) is None
    assert q("SELECT 1 FROM audit_logs WHERE action='del_timetable'", one=True)
    _clear(uid)


def test_admin_timetable_page_shows_coverage(admin_client, setup_students):
    """管理頁第一眼要看到「多少人的課表還是假的」。"""
    uid = setup_students
    html = admin_client.get(f'/admin/timetable?uid={uid}').get_data(as_text=True)
    assert '覆蓋率' in html or '示範資料' in html, html[:400]
    _clear(uid)


# --------------------------------------------------------------------------
# 檢視權限
# --------------------------------------------------------------------------
def test_student_sees_own_timetable(setup_students):
    uid = setup_students
    apply_timetable(parse_timetable_csv(CSV_A), mode='replace')
    html = _login(STU_A).get('/timetable').get_data(as_text=True)
    assert 'V17-A-AM' in html and 'V17-A-WED' in html
    assert '示範資料' not in html, '匯入真實課表後不該再顯示示範資料警示'
    _clear(uid)


def test_demo_only_timetable_is_flagged(setup_students):
    """課表全是示範資料時必須明白講，否則「受影響課程」是假的還被當真。"""
    uid_b = _uid(STU_B)
    execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
            "course_code, source) VALUES (?,0,'08:00','09:00','DEMO-ONLY','demo')",
            (uid_b,))
    html = _login('T002', DEMO).get(f'/timetable/{uid_b}').get_data(as_text=True)
    assert '示範資料' in html and 'DEMO-ONLY' in html
    _clear(uid_b)


def test_advisor_cannot_view_other_advisors_student(setup_students):
    """導師看別的導師的學生課表：擋下（課表與假單一樣是個人資料）。"""
    uid_a = setup_students
    apply_timetable(parse_timetable_csv(CSV_A), mode='replace')
    html = _login('T002', DEMO).get(f'/timetable/{uid_a}',
                                    follow_redirects=True).get_data(as_text=True)
    assert '你沒有權限查看該學生的課表' in html
    assert 'V17-A-AM' not in html
    _clear(uid_a)


def test_advisor_sees_own_student(setup_students):
    uid_a = setup_students
    apply_timetable(parse_timetable_csv(CSV_A), mode='replace')
    html = _login('T001', DEMO).get(f'/timetable/{uid_a}').get_data(as_text=True)
    assert 'V17-A-AM' in html
    _clear(uid_a)


def test_timetable_page_lists_visible_students(setup_students):
    """沒指定學生時給名單；名單也必須按範圍過濾。"""
    setup_students
    html = _login('T001', DEMO).get('/timetable').get_data(as_text=True)
    assert '選擇學生查看課表' in html
    assert STU_A in html and STU_B not in html, '名單不得出現別的導師的學生'


def test_seed_timetable_marks_demo(setup_students):
    """示範資料必須標 source='demo'，這是覆蓋率統計的唯一依據。"""
    demo = q("SELECT COUNT(*) c FROM timetable WHERE source='demo'", one=True)['c']
    assert demo > 0, '示範課表應標記為 demo'
