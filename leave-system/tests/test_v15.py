# -*- coding: utf-8 -*-
"""v1.5.0 測試：校曆檔匯入（學期 ＋ 假日 ＋ 補課日）。

延續 test_v14 的慣例：自建專用資料、字母序上最後執行（本檔 v15 > v14），
匯入真實校曆不會影響先前測試。

解析（純函式）與寫入（資料庫）分開測：parse_calendar_text 不碰資料庫，
可以直接餵各種格式；apply_calendar / 路由則驗證真的寫進去了、且重複匯入
不會長出重複資料。
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import app, q, execute, is_school_off, affected_courses_for  # noqa: E402
from calendar_import import (parse_calendar_text, apply_calendar,   # noqa: E402
                             load_preset, preset_for_today, PRESETS)

DEMO = 'Demo@2026'
STU = '2024001'          # 種子學生：借用來驗「受影響課程」
PRESET = 'hkustgz-2026-27'


@pytest.fixture(autouse=True)
def _app_ctx():
    with app.app_context():
        yield


@pytest.fixture()
def admin_client():
    c = app.test_client()
    assert c.post('/login', data={'username': 'admin',
                                  'password': DEMO}).status_code == 302
    return c


def _timetable(student_username, weekday, code):
    """替指定學生插一筆課表（週五有課，用來驗補課日）。"""
    uid = q('SELECT id FROM users WHERE username=?', (student_username,), one=True)['id']
    execute('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
            "course_code, kind, room, note) VALUES (?,?,?,?,?,'Lecture','A101','') "
            'ON CONFLICT DO NOTHING',
            (uid, weekday, '09:00', '10:30', code))
    return uid


# --------------------------------------------------------------------------
# 解析（純函式）
# --------------------------------------------------------------------------
def test_parse_terms_holidays_makeups():
    text = """
    # 註解行
    term    2026-09-01  2026-12-25  2026-27 秋季學期
    holiday 2026-10-01  2026-10-03  國慶節
    holiday 2027-06-09              端午節
    makeup  2026-10-10  fri         補星期五課程
    """
    r = parse_calendar_text(text)
    assert not r['errors']
    assert r['terms'] == [{'label': '2026-27 秋季學期',
                           'start_date': '2026-09-01', 'end_date': '2026-12-25'}]
    # 區間要展開成逐日，單日就是一天
    assert [h['day'] for h in r['holidays']] == ['2026-10-01', '2026-10-02',
                                                 '2026-10-03', '2027-06-09']
    assert r['holidays'][0]['label'] == '國慶節'
    assert r['makeups'] == [{'day': '2026-10-10', 'cover_weekday': 4,
                             'label': '補星期五課程'}]


def test_parse_weekday_aliases():
    for token in ('fri', 'FRIDAY', '週五', '周五', '星期五', '5', '2026-10-09'):
        r = parse_calendar_text(f'makeup 2026-10-10 {token} 補課')
        assert not r['errors'], f'{token} 應可辨識'
        assert r['makeups'][0]['cover_weekday'] == 4


def test_parse_bad_lines_reported_not_fatal():
    text = ('term 2026-09-01 2026-12-25 2026-27 秋季學期\n'
            'holiday 2026-13-45 壞日期\n'
            'holiday 2026-10-05 2026-10-01 結束日早於起始日\n'
            'makeup 2026-10-11 sunday 週末沒課可補\n'
            'whatever 2026-01-01 不認識的類型\n'
            'term 2026-09-01 缺欄位\n')
    r = parse_calendar_text(text)
    # 對的照樣收，錯的進 errors
    assert len(r['terms']) == 1
    assert len(r['errors']) == 5
    assert all(e['line'] and e['msg'] for e in r['errors'])


def test_parse_empty_and_comments_only():
    r = parse_calendar_text('# 只有註解\n\n   \n')
    assert not (r['terms'] or r['holidays'] or r['makeups'] or r['errors'])


# --------------------------------------------------------------------------
# 內建校曆檔
# --------------------------------------------------------------------------
def test_bundled_hkustgz_calendar():
    text = load_preset(PRESET)
    assert text and 'hkust-gz.edu.cn' in text      # 檔頭要有來源
    r = parse_calendar_text(text)
    assert not r['errors']
    assert len(r['terms']) == 4                     # 秋 / 冬 / 春 / 夏
    labels = [t['label'] for t in r['terms']]
    assert labels[0] == '2026-27 秋季學期'
    days = [h['day'] for h in r['holidays']]
    assert '2026-09-25' in days and '2026-09-27' in days      # 中秋 9/25–27
    assert sum(1 for d in days if d.startswith('2026-10-0')) == 7   # 國慶 10/1–7
    assert '2026-12-25' not in days                 # 聖誕不在校曆的公眾假期裡
    assert r['makeups'] == [{'day': '2026-10-10', 'cover_weekday': 4,
                             'label': '補星期五課程（國慶假期調課）'}]


def test_preset_for_today():
    # 今天是 2026-11-02，落在 2026-27 秋季學期內 → 應配對到內建校曆
    assert preset_for_today(datetime.date(2026, 11, 2)) == PRESET
    assert preset_for_today(datetime.date(2030, 1, 1)) is None
    assert PRESETS[PRESET][1].startswith('港科大')


def test_load_preset_rejects_unknown_name():
    assert load_preset('../../etc/passwd') is None
    assert load_preset('不存在') is None


# --------------------------------------------------------------------------
# 匯入（資料庫）
# --------------------------------------------------------------------------
def test_import_calendar_route_and_idempotent(admin_client):
    text = ('term 2099-01-01 2099-06-30 2099 測試學期\n'
            'holiday 2099-05-01 2099-05-03 勞動節\n'
            'makeup 2099-05-08 mon 補星期一課程\n')
    r = admin_client.post('/admin/calendar/import', data={'calendar_text': text})
    assert r.status_code == 302
    assert q('SELECT id FROM terms WHERE label=?', ('2099 測試學期',), one=True)
    assert len(q("SELECT * FROM holidays WHERE day LIKE '2099-05-%'")) == 3
    mk = q('SELECT * FROM makeup_days WHERE day=?', ('2099-05-08',), one=True)
    assert mk['cover_weekday'] == 0

    # 再匯一次：冪等，不該長出重複列
    admin_client.post('/admin/calendar/import', data={'calendar_text': text})
    assert len(q("SELECT * FROM holidays WHERE day LIKE '2099-05-%'")) == 3
    assert len(q('SELECT * FROM makeup_days')) == 1

    # 清場（避免污染後面的測試）
    execute('DELETE FROM terms WHERE label=?', ('2099 測試學期',))
    execute("DELETE FROM holidays WHERE day LIKE '2099-%'")
    execute("DELETE FROM makeup_days WHERE day LIKE '2099-%'")


def test_import_preset_route(admin_client):
    r = admin_client.post('/admin/calendar/import', data={'preset': PRESET})
    assert r.status_code == 302
    assert q('SELECT id FROM terms WHERE label=?', ('2026-27 秋季學期',), one=True)
    assert q('SELECT 1 FROM holidays WHERE day=? AND label=?',
             ('2026-10-01', '國慶節'), one=True)
    assert q('SELECT 1 FROM makeup_days WHERE day=?', ('2026-10-10',), one=True)


def test_import_unknown_preset_is_rejected(admin_client):
    r = admin_client.post('/admin/calendar/import', data={'preset': 'nope'},
                          follow_redirects=True)
    assert r.status_code == 200


def test_apply_calendar_sets_current_term_when_missing():
    # 目前學期已被前面的測試／匯入設定時，apply_calendar 不該擅自改
    before = q('SELECT id FROM terms WHERE is_current=1', one=True)
    stats = apply_calendar(parse_calendar_text(
        'term 2098-01-01 2098-06-30 2098 學期\n'))
    assert stats['terms'] == 1
    after = q('SELECT id FROM terms WHERE is_current=1', one=True)
    assert (before['id'] if before else None) == (after['id'] if after else None)
    execute('DELETE FROM terms WHERE label=?', ('2098 學期',))


# --------------------------------------------------------------------------
# 補課日與假日的實際效果（C3）
# --------------------------------------------------------------------------
def test_makeup_day_counts_as_school_day(admin_client):
    """10/10（六）補星期五的課：請假要算缺課，且算的是星期五的課。"""
    admin_client.post('/admin/calendar/import', data={'preset': PRESET})
    uid = _timetable(STU, weekday=4, code='MUKE100')   # 週五補插一門課

    saturday = datetime.date(2026, 10, 10)         # 週六，但是補課日
    assert saturday.weekday() == 5
    assert is_school_off(saturday) is False

    hit = affected_courses_for(uid, '2026-10-10', '2026-10-10')
    assert len(hit) == 1                            # 補課日沒被當週末跳過
    assert hit[0]['weekday_label'] == '週五'        # 用被補那天（五）的課表
    # 回傳的正是「星期五」課表：種子學生週五本來就有課，故不寫死
    # 索引／課號，直接和資料庫裡的週五課表做集合比對，並確認新插的
    # MUKE100 也在其中（timetable 無 UNIQUE，兩筆會並存）。
    codes = {c['course_code'] for c in hit[0]['courses']}
    assert 'MUKE100' in codes
    friday = {r['course_code'] for r in q(
        'SELECT course_code FROM timetable WHERE student_id=? AND weekday=4',
        (uid,))}
    assert codes == friday

    # 對照：國慶假期內的星期五 → 放假，不列受影響課程
    assert is_school_off(datetime.date(2026, 10, 2)) is True
    assert affected_courses_for(uid, '2026-10-02', '2026-10-02') == []
    # 對照：一般的星期五 → 照常列
    assert len(affected_courses_for(uid, '2026-10-09', '2026-10-09')) == 1


def test_delete_makeup_route(admin_client):
    execute('INSERT INTO makeup_days (day, cover_weekday, label) '
            "VALUES ('2097-03-01', 2, '待刪除') "
            'ON CONFLICT(day) DO NOTHING')
    mid = q('SELECT id FROM makeup_days WHERE day=?', ('2097-03-01',), one=True)['id']
    r = admin_client.post(f'/admin/makeup/{mid}/delete')
    assert r.status_code == 302
    assert not q('SELECT 1 FROM makeup_days WHERE id=?', (mid,), one=True)
