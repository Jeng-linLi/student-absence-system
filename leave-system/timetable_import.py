# -*- coding: utf-8 -*-
"""課表 CSV 解析與匯入（v1.7.0 · C1）。

v1.6.0 以前，課表是「所有學生共用同一份 DEMO_TIMETABLE」——`seed_timetable()`
把同一組課複製給每一個學生。這讓「課表感知受影響課程」這個賣點建立在
「每個人的課都一樣」的錯誤前提上：算出來的受影響課程，對任何一個真實學生
都可能是錯的。

本模組把課表變成「逐生、可匯入、可編輯」的真資料：

    parse_timetable_csv(text)   → 純函式，不碰資料庫（可單測）
    apply_timetable(parsed)     → 寫入資料庫（需在 app context 內呼叫）

檔案格式：CSV，一列一節課。`#` 開頭為註解，空白列忽略。
表頭可省略——省略時按下列順序解讀欄位：

    student_no, weekday, start_time, end_time, course_code, kind, room, note
    2024001,    mon,     09:00,      10:20,    AIAA 2205-L01, Lecture, Rm 102, W4

表頭欄名盡量寬鬆（中英文、簡繁、常見別名都收），例如：

    學號,星期,開始時間,結束時間,課程代碼,類型,教室,備註
"""
import csv

from calendar_import import WEEKDAY_ALIASES   # 星期別名集中在一處，兩邊一致

MAX_COURSE_CODE = 40
MAX_KIND_LEN = 20
MAX_ROOM_LEN = 60
MAX_NOTE_LEN = 80
MAX_IMPORT_ROWS = 5000      # 單次匯入上限：5,000 節課遠超過任何一個學生的課表

# 欄位別名：key 是內部欄名，value 是可接受的表頭寫法。
# 收簡體與常見英文別名是為了「教務處匯出的檔直接貼」——要求對方改成
# 特定欄名，是最容易讓匯入功能被放棄的細節。
COLUMN_ALIASES = {
    'student_no': ('student_no', 'studentno', 'student_id', 'studentid', 'sid',
                   'username', 'user', 'student', '學號', '学号', '帳號', '账号'),
    'weekday': ('weekday', 'week_day', 'day', 'day_of_week', 'dow', 'week',
                '星期', '周', '星期幾', '星期几'),
    'start_time': ('start_time', 'starttime', 'start', 'begin', 'begin_time',
                   'from', '開始', '开始', '開始時間', '开始时间', '上課時間', '上课时间'),
    'end_time': ('end_time', 'endtime', 'end', 'finish', 'finish_time', 'to',
                 '結束', '结束', '結束時間', '结束时间', '下課時間', '下课时间'),
    'course_code': ('course_code', 'coursecode', 'course', 'code', 'course_id',
                    'course_name', 'subject', '課程', '课程', '課程代碼', '课程代码',
                    '課程名稱', '课程名称', '科目'),
    'kind': ('kind', 'type', 'category', 'session_type', '類型', '类型',
             '類別', '类别', '課型', '课型'),
    'room': ('room', 'location', 'venue', 'classroom', '教室', '地點', '地点',
             '上課地點', '上课地点'),
    'note': ('note', 'remark', 'remarks', 'comment', 'memo', '備註', '备注',
             '說明', '说明'),
}

# 省略表頭時的解讀順序
POSITIONAL_FIELDS = ('student_no', 'weekday', 'start_time', 'end_time',
                     'course_code', 'kind', 'room', 'note')


class TimetableRowError(ValueError):
    """單列格式錯誤。"""


def _norm(s):
    """表頭比對用的正規化：小寫、去空白與底線／連字號。"""
    return (s or '').strip().lower().replace(' ', '').replace('_', '').replace('-', '')


# 別名對照表（正規化後）：只在載入時算一次
_ALIAS_LOOKUP = {}
for _field, _names in COLUMN_ALIASES.items():
    for _n in _names:
        _ALIAS_LOOKUP[_norm(_n)] = _field


# calendar_import 的 WEEKDAY_ALIASES 只收週一到週五（校曆的星期一律指上課日）。
# 課表匯入要能分辨「週末」與「打錯字」——兩者該給管理員的提示不同，
# 前者是「這列本來就不該出現」，後者是「改一下拼法」。
WEEKEND_ALIASES = {
    '6': 5, 'sat': 5, 'saturday': 5, '週六': 5, '周六': 5, '星期六': 5,
    '7': 6, 'sun': 6, 'sunday': 6, '週日': 6, '周日': 6, '星期日': 6, '週天': 6,
}


def _parse_weekday(s):
    """`mon` / `週一` / `1` → weekday（0=週一 … 4=週五）。"""
    key = (s or '').strip().lower()
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    if key in WEEKEND_ALIASES:
        raise TimetableRowError(f'{s} 是週末，課表只接受週一到週五')
    raise TimetableRowError(f'無法辨識的星期：{s}（可用 mon / 週一 / 1）')


def _parse_time(s):
    """`9:00` / `09:00` → `09:00`；格式錯誤擲 TimetableRowError。"""
    raw = (s or '').strip()
    parts = raw.split(':')
    if len(parts) != 2:
        raise TimetableRowError(f'時間格式需為 HH:MM：{raw}')
    h, m = parts
    if not (h.isdigit() and m.isdigit()):
        raise TimetableRowError(f'時間格式需為 HH:MM：{raw}')
    hh, mm = int(h), int(m)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise TimetableRowError(f'時間超出範圍：{raw}')
    return f'{hh:02d}:{mm:02d}'


def pad_time(s):
    """`9:00` → `09:00`。格式不合時原樣回傳（呼叫端應先以 is_hhmm 驗證）。

    時間一律以零填充的 HH:MM 存庫：`'9:00' < '12:00'` 是 False，
    不補零的話「上午／下午」的判斷（affected_courses_for 用 start<'12:00'
    分辨）會把九點的課判成下午的課。
    """
    try:
        return _parse_time(s)
    except TimetableRowError:
        return s


def _map_header(cells):
    """把表頭列轉成「欄名 → 欄索引」對映；認不出來就回傳 None（視為無表頭）。"""
    mapping = {}
    for i, cell in enumerate(cells):
        field = _ALIAS_LOOKUP.get(_norm(cell))
        if field and field not in mapping:
            mapping[field] = i
    # 至少要認出「學生」與「課程」才當它是表頭——否則只是剛好有幾個常見詞
    if 'student_no' in mapping and 'course_code' in mapping:
        return mapping
    return None


def parse_timetable_csv(text):
    """解析課表 CSV。

    回傳 dict：rows（合法列）、errors（有錯的列）。
    解析不會因單列錯誤中止——錯的列出來讓管理員改，對的照樣匯入，
    與校曆檔匯入（calendar_import）保持一致的行為。
    """
    result = {'rows': [], 'errors': []}

    def err(lineno, raw, msg):
        result['errors'].append({'line': lineno, 'raw': raw, 'msg': msg})

    lines = (text or '').splitlines()
    # 去掉註解與空白列，但保留原始行號以便回報錯誤
    numbered = [(i, ln) for i, ln in enumerate(lines, 1)
                if ln.strip() and not ln.strip().startswith('#')]
    if not numbered:
        return result

    reader = list(csv.reader([ln for _i, ln in numbered]))
    mapping = _map_header(reader[0]) if reader else None
    start = 1 if mapping else 0

    if len(numbered) - start > MAX_IMPORT_ROWS:
        err(numbered[start][0], f'共 {len(numbered) - start} 列',
            f'超過單次匯入上限 {MAX_IMPORT_ROWS} 列，請分批匯入')
        return result

    for idx in range(start, len(numbered)):
        lineno = numbered[idx][0]
        cells = [c.strip() for c in reader[idx]]
        if not any(cells):
            continue
        if mapping:
            def cell(field):
                i = mapping.get(field)
                return cells[i] if i is not None and i < len(cells) else ''
        else:
            def cell(field):
                i = POSITIONAL_FIELDS.index(field)
                return cells[i] if i < len(cells) else ''

        raw_line = numbered[idx][1].strip()
        try:
            student_no = cell('student_no').strip()
            if not student_no:
                raise TimetableRowError('學號不可空白')
            if len(student_no) > 50:
                raise TimetableRowError('學號過長（上限 50 字）')
            wd = _parse_weekday(cell('weekday'))
            start_t = _parse_time(cell('start_time'))
            end_t = _parse_time(cell('end_time'))
            if end_t <= start_t:
                raise TimetableRowError(f'結束時間 {end_t} 需晚於開始 {start_t}')
            code = cell('course_code').strip()
            if not code:
                raise TimetableRowError('課程代碼不可空白')
            result['rows'].append({
                'student_no': student_no,
                'weekday': wd,
                'start_time': start_t,
                'end_time': end_t,
                'course_code': code[:MAX_COURSE_CODE],
                'kind': cell('kind').strip()[:MAX_KIND_LEN],
                'room': cell('room').strip()[:MAX_ROOM_LEN],
                'note': cell('note').strip()[:MAX_NOTE_LEN],
            })
        except (TimetableRowError, ValueError, IndexError) as exc:
            err(lineno, raw_line, str(exc))
    return result


def _insert_row(sid, r, source):
    """寫入一節課；已存在同一時段同一門課則更新（冪等）。

    依賴 timetable 上的唯一索引 (student_id, weekday, start_time, end_time,
    course_code)。舊庫若因既有重複資料而建不出索引，退回去重再寫入——
    寧可多一次 DELETE，也不要讓匯入整批失敗。
    """
    from db import execute
    sql = ('INSERT INTO timetable (student_id, weekday, start_time, end_time, '
           'course_code, kind, room, note, source) VALUES (?,?,?,?,?,?,?,?,?)')
    args = (sid, r['weekday'], r['start_time'], r['end_time'], r['course_code'],
            r['kind'], r['room'], r['note'], source)
    try:
        execute(sql + ' ON CONFLICT(student_id, weekday, start_time, end_time, '
                'course_code) DO UPDATE SET kind=excluded.kind, '
                'room=excluded.room, note=excluded.note, source=excluded.source',
                args)
    except Exception:
        # 唯一索引不存在（舊庫有重複資料，建索引時被略過）→ 手動去重再寫入
        execute('DELETE FROM timetable WHERE student_id=? AND weekday=? '
                'AND start_time=? AND end_time=? AND course_code=?',
                (sid, r['weekday'], r['start_time'], r['end_time'],
                 r['course_code']))
        execute(sql, args)


def apply_timetable(parsed, mode='replace', source='import'):
    """把解析結果寫入資料庫。呼叫端應包在 atomic() 內。

    mode='replace'（預設）：對「檔案中出現的每一位學生」，先清空他既有的
    課表再寫入——對應教務處匯出整份課表的情境，舊課表不會殘留。
    mode='merge'：只新增／更新檔案裡的課，其餘維持不動（增量補課用）。

    學號查不到對應學生帳號時整列略過，不寫入、不中斷其餘列——
    匯入一份全年級的課表時，有幾個休學／退選的學號是常態。

    回傳 stats：students（實際寫入的學生數）、rows（寫入節數）、
    deleted（replace 模式清掉的舊課數）、unknown（查不到的學號）。
    """
    from db import q, execute      # 延遲匯入：本模組也要能被純單測載入

    stats = {'students': 0, 'rows': 0, 'deleted': 0, 'unknown': []}
    if mode not in ('replace', 'merge'):
        raise ValueError('mode 只能是 replace 或 merge')

    rows = parsed.get('rows') or []
    if not rows:
        return stats

    # 一次把會用到的學生查出來，不在迴圈裡逐列查庫
    wanted = {r['student_no'] for r in rows}
    ph = ','.join('?' * len(wanted))
    users = {u['username']: u['id'] for u in q(
        f"SELECT id, username FROM users WHERE role='student' AND active=1 "
        f'AND username IN ({ph})', tuple(wanted))}

    by_student = {}
    for r in rows:
        sid = users.get(r['student_no'])
        if sid is None:
            if r['student_no'] not in stats['unknown']:
                stats['unknown'].append(r['student_no'])
            continue
        by_student.setdefault(sid, []).append(r)

    for sid, srows in by_student.items():
        if mode == 'replace':
            stats['deleted'] += q('SELECT COUNT(*) c FROM timetable '
                                  'WHERE student_id=?', (sid,), one=True)['c']
            execute('DELETE FROM timetable WHERE student_id=?', (sid,))
        for r in srows:
            _insert_row(sid, r, source)
            stats['rows'] += 1
        stats['students'] += 1
    return stats


def timetable_coverage():
    """課表覆蓋率統計（管理頁警示用）。

    回傳 dict：students（在學學生數）、with_tt（有課表的人數）、
    demo_only（課表全是示範資料的人數）、imported（含匯入或手動課表的人數）。
    「所有人共用同一份示範課表」是可演示但不可交付的狀態，必須讓管理員
    一眼看見。
    """
    from db import q
    total = q("SELECT COUNT(*) c FROM users WHERE role='student' AND active=1",
              one=True)['c']
    rows = q("SELECT student_id, SUM(CASE WHEN source='demo' THEN 1 ELSE 0 END) demo_n, "
             'COUNT(*) n FROM timetable GROUP BY student_id')
    with_tt = len(rows)
    demo_only = sum(1 for r in rows if r['demo_n'] == r['n'])
    return {'students': total, 'with_tt': with_tt, 'demo_only': demo_only,
            'imported': with_tt - demo_only, 'missing': max(total - with_tt, 0)}
