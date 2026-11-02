# -*- coding: utf-8 -*-
"""校曆檔解析與匯入（v1.5.0 · C3）。

教務處發的校曆通常就是一份日期清單：學期起訖、公眾假期、調補課日。
把它抄成一份純文字檔，管理員貼進來（或一鍵帶入內建檔）就能一次寫入，
不必在管理介面逐筆新增——v1.4.0 的批次匯入只吃「一天一行」，碰到
「10/1–10/7 國慶節」這種區間還是得自己展開七天。

本模組刻意切成兩半，讓解析邏輯可以脫離資料庫單測：

    parse_calendar_text(text)  → 純函式，不碰資料庫
    apply_calendar(parsed)     → 寫入資料庫（需在 app context 內呼叫）

檔案格式（`#` 開頭為註解，空白行忽略）：

    term    <起始日> <結束日> <學期標籤>
    holiday <起始日> [結束日] <名稱>           # 省略結束日＝單日
    makeup  <補課日> <被補的星期|日期> <名稱>  # 例：fri / 週五 / 2026-10-02
"""
from datetime import date, timedelta
import os

# 內建校曆檔：管理員可以一鍵帶入，不必自己找 PDF 抄日期。
# key 是對外名稱（出現在表單值裡），value 是 data/ 下的檔名——
# 用白名單對應而不是直接接受路徑，避免被塞進 ../../ 之類的東西。
CALENDAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PRESETS = {
    'hkustgz-2026-27': ('hkustgz-2026-27.txt', '港科大（廣州）2026-27 學年'),
}

# 補課日「補星期幾」的寫法盡量寬鬆：英文縮寫、中文、數字都收。
# 值為 Python weekday（週一=0 … 週五=4）——週末不收，因為週末本來就沒課可補。
WEEKDAY_ALIASES = {
    '1': 0, 'mon': 0, 'monday': 0, '週一': 0, '周一': 0, '星期一': 0,
    '2': 1, 'tue': 1, 'tuesday': 1, '週二': 1, '周二': 1, '星期二': 1,
    '3': 2, 'wed': 2, 'wednesday': 2, '週三': 2, '周三': 2, '星期三': 2,
    '4': 3, 'thu': 3, 'thursday': 3, '週四': 3, '周四': 3, '星期四': 3,
    '5': 4, 'fri': 4, 'friday': 4, '週五': 4, '周五': 4, '星期五': 4,
}

MAX_TERM_LABEL = 40      # 與 schema / 表單校驗一致
MAX_LABEL_LEN = 60
MAX_RANGE_DAYS = 366     # 單筆假日區間上限：超過一定是填錯了


class CalendarEntryError(ValueError):
    """單行格式錯誤。"""


def _parse_date(s):
    return date.fromisoformat(s.strip())


def _is_date(s):
    try:
        _parse_date(s)
        return True
    except (ValueError, AttributeError):
        return False


def _parse_weekday(s):
    """把 `fri` / `週五` / `2026-10-02` 轉成 weekday（0=週一）。"""
    key = s.strip().lower()
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    if _is_date(s):                      # 直接給日期 → 取那天是星期幾
        wd = _parse_date(s).weekday()
        if wd > 4:
            raise CalendarEntryError(f'{s} 是週末，沒有可補的課')
        return wd
    raise CalendarEntryError(f'無法辨識的星期／日期：{s}')


def _expand(start, end):
    """含端點的日期區間 → 逐日清單。"""
    if end < start:
        raise CalendarEntryError(f'結束日 {end} 早於起始日 {start}')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise CalendarEntryError(f'區間超過 {MAX_RANGE_DAYS} 天')
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def parse_calendar_text(text):
    """解析校曆檔。

    回傳 dict：terms / holidays / makeups 三種項目，外加 errors（有錯的行）。
    解析不會因單行錯誤中止——錯的列出來讓管理員改，對的照樣匯入。
    """
    result = {'terms': [], 'holidays': [], 'makeups': [], 'errors': []}

    def err(lineno, raw, msg):
        result['errors'].append({'line': lineno, 'raw': raw.strip(), 'msg': msg})

    for lineno, raw in enumerate((text or '').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(None, 1)
        kind = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ''
        try:
            if kind == 'term':
                tok = rest.split()
                if len(tok) < 3:
                    raise CalendarEntryError('term 需要「起始日 結束日 標籤」')
                start, end = _parse_date(tok[0]), _parse_date(tok[1])
                label = ' '.join(tok[2:]).strip()
                if not label:
                    raise CalendarEntryError('學期標籤不可空白')
                if len(label) > MAX_TERM_LABEL:
                    raise CalendarEntryError(
                        f'學期標籤過長（上限 {MAX_TERM_LABEL} 字）')
                if end < start:
                    raise CalendarEntryError('學期結束日早於起始日')
                result['terms'].append({'label': label,
                                        'start_date': start.isoformat(),
                                        'end_date': end.isoformat()})

            elif kind == 'holiday':
                tok = rest.split()
                if len(tok) < 2:
                    raise CalendarEntryError('holiday 需要「日期 [結束日] 名稱」')
                start = _parse_date(tok[0])
                if _is_date(tok[1]):                 # 給了結束日 → 區間
                    end = _parse_date(tok[1])
                    label = ' '.join(tok[2:]).strip()
                else:                                # 第二個欄位不是日期 → 單日
                    end = start
                    label = ' '.join(tok[1:]).strip()
                for d in _expand(start, end):
                    result['holidays'].append(
                        {'day': d.isoformat(), 'label': label[:MAX_LABEL_LEN]})

            elif kind == 'makeup':
                tok = rest.split()
                if len(tok) < 2:
                    raise CalendarEntryError('makeup 需要「補課日 星期 名稱」')
                day = _parse_date(tok[0])
                cover = _parse_weekday(tok[1])
                label = ' '.join(tok[2:]).strip()
                result['makeups'].append(
                    {'day': day.isoformat(), 'cover_weekday': cover,
                     'label': label[:MAX_LABEL_LEN]})

            else:
                raise CalendarEntryError(f'不認識的類型：{kind}')
        except (ValueError, CalendarEntryError) as exc:
            err(lineno, raw, str(exc))
    return result


def apply_calendar(parsed, set_current=True):
    """把解析結果寫入資料庫（冪等：重複匯入覆蓋同名／同日資料）。

    set_current：若目前沒有任何學期被標為「目前學期」，就自動把涵蓋今天的
    那個學期設為目前——匯入整份校曆後卻沒有目前學期，儀表板與統計會全部落空。
    已經有目前學期時不動它，避免覆蓋管理員的設定。
    """
    from db import q, execute          # 延遲匯入：本模組也要能被純單測載入

    stats = {'terms': 0, 'holidays': 0, 'makeups': 0}

    for t in parsed.get('terms', []):
        execute('INSERT INTO terms (label, start_date, end_date, is_current) '
                'VALUES (?,?,?,0) '
                'ON CONFLICT(label) DO UPDATE SET '
                'start_date=excluded.start_date, end_date=excluded.end_date',
                (t['label'], t['start_date'], t['end_date']))
        stats['terms'] += 1

    for h in parsed.get('holidays', []):
        execute('INSERT INTO holidays (day, label) VALUES (?,?) '
                'ON CONFLICT(day) DO UPDATE SET label=excluded.label',
                (h['day'], h['label']))
        stats['holidays'] += 1

    for m in parsed.get('makeups', []):
        execute('INSERT INTO makeup_days (day, cover_weekday, label) '
                'VALUES (?,?,?) '
                'ON CONFLICT(day) DO UPDATE SET '
                'cover_weekday=excluded.cover_weekday, label=excluded.label',
                (m['day'], m['cover_weekday'], m['label']))
        stats['makeups'] += 1

    if set_current and parsed.get('terms') \
            and not q('SELECT 1 FROM terms WHERE is_current=1'):
        today = date.today().isoformat()
        row = q('SELECT id FROM terms WHERE start_date<=? AND end_date>=? '
                'ORDER BY start_date LIMIT 1', (today, today), one=True)
        if row:
            execute('UPDATE terms SET is_current=1 WHERE id=?', (row['id'],))
    return stats


def load_preset(name):
    """讀取內建校曆檔的內容；名稱不在白名單內就回傳 None。"""
    entry = PRESETS.get(name)
    if not entry:
        return None
    path = os.path.join(CALENDAR_DIR, entry[0])
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def preset_for_today(today=None):
    """找出涵蓋今天的內建校曆檔名稱（沒有就回傳 None）。

    開新庫時用它決定要匯入哪一份校曆——學年往前走、data/ 裡放了新學年的
    檔案，就不用改程式碼。
    """
    today = today or date.today()
    iso = today.isoformat()
    for name, (filename, _label) in PRESETS.items():
        text = load_preset(name)
        if not text:
            continue
        for t in parse_calendar_text(text)['terms']:
            if t['start_date'] <= iso <= t['end_date']:
                return name
    return None
