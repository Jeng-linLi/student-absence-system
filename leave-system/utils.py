# -*- coding: utf-8 -*-
"""純函式與常數：不依賴 Flask / 資料庫，便於單元測試。"""
import os
import re
import secrets
from datetime import date, datetime

# 審批分流門檻：超過此天數需院系複核
FACULTY_THRESHOLD_DAYS = 3
ALLOWED_EXT = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

# 表單校驗邊界
MAX_BACKDATE_DAYS = 30     # 最多可回溯補請的天數（病假補件等情境）
MAX_SINGLE_DAYS = 90       # 單張假單天數上限，防止日期填錯
MAX_ATTACHMENT_MB = 8
# v1.18.0：課表預覽 API 是匿名成本的 GET，區間必須有上限。
# 放寬到單張假單上限的 4 倍（360 天）仍可預覽整學年，但擋掉
# 2000-01-01～2099-12-31 這種 3.6 萬次逐日迴圈的請求。
MAX_AFFECTED_DAYS = 360

# 自由文字長度上限（防止超長內容撐爆版面／儲存）
MAX_REASON_LEN = 500
MAX_COMMENT_LEN = 300
MAX_PHONE_LEN = 30
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128   # 上限：超長密碼只會拖慢雜湊運算，沒有安全增益

# 登入限流
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SEC = 300

# 學年起始月份（9 月），用於統計「本學年已核准天數」
ACADEMIC_YEAR_START_MONTH = 9

# 原型版本號（模板頁腳從此帶入，避免各處硬編碼後失真）
APP_VERSION = '1.19.0'

# 假單狀態白名單（與 schema.sql 的 CHECK 約束一致）
LEAVE_STATUSES = ('pending', 'approved', 'rejected', 'withdrawn')

# 常見弱密碼擋鎖：通過組成策略（長度＋大小寫＋數字）仍不許用。
# 比對時忽略大小寫。這份清單收錄公開外洩密碼檔中最高頻的組合力——
# 舊版示範預設密碼 Pass@123 正在其中，因此 v0.9.0 起示範密碼改為 Demo@2026。
COMMON_PASSWORDS = frozenset({
    'pass@123', 'pass@1234', 'password', 'password1', 'password123',
    'passw0rd', 'p@ssw0rd', '12345678', '123456789', '1234567890',
    '87654321', '00000000', '11111111', 'qwerty123', 'qwertyuiop',
    'abc12345', 'abcdefgh', 'abcdefg1', 'a1234567', 'aa123456',
    'admin123', 'admin1234', 'administrator', 'letmein1', 'letmein123',
    'welcome1', 'welcome123', 'iloveyou1', 'monkey123', 'dragon123',
    'sunshine1', 'princess1', 'football1', 'baseball1', 'shadow123',
    'master123', 'superman1', 'trustno1', 'student1', 'teacher123',
})

# 角色白名單（與 schema.sql 的 CHECK 約束一致）
VALID_ROLES = ('student', 'advisor', 'faculty', 'admin')
_USERNAME_RE = re.compile(r'[A-Za-z0-9._@-]{1,50}')

# 假別代號格式與名稱長度上限
_TYPE_CODE_RE = re.compile(r'[A-Za-z0-9_-]{1,20}')
MAX_TYPE_NAME_LEN = 30

# 單次批次審批上限（v1.6.0）：一筆交易別鎖太久，也防誤勾整頁
MAX_BULK_DECIDE = 50

# 列表搜尋關鍵字上限（v1.13.0）：避免超長輸入拖慢 LIKE 掃描與撐爆版面
MAX_SEARCH_Q_LEN = 80

# 閒置 session 逾時（分鐘）：超過此時間無操作即需重新登入。
# session 是簽名 cookie，服務端無法主動逐出，只能以時間戳記在每次請求時檢驗。
SESSION_IDLE_MINUTES = int(os.environ.get('LEAVE_SESSION_IDLE_MINUTES', '480'))


def is_hhmm(s):
    """是否為合法的 24 小時制 HH:MM（課表起訖時間，v1.7.0）。

    不要求補零（`9:00` 也算）：Excel 匯出的時間常是這種寫法，
    要求使用者補成 `09:00` 只會讓課表匯入被放棄。寫庫前統一由
    timetable_import._parse_time() 補零成 `09:00`，避免字串比較
    （`'9:00' < '12:00'` 為 False）把上午／下午的判斷搞反。
    """
    if not isinstance(s, str) or s.count(':') != 1:
        return False
    h, m = s.split(':')
    if not (h.isdigit() and m.isdigit()):
        return False
    return 0 <= int(h) <= 23 and 0 <= int(m) <= 59


def parse_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


# --------------------------------------------------------------------------
# 列表搜尋（E4 · v1.13.0）
# --------------------------------------------------------------------------
def parse_list_filters(args):
    """正規化 `q`／`date_from`／`date_to` 三個列表篩選參數。

    回傳 dict，欄位一律是字串（空字串代表不篩選）：
      - `q`：去除前後空白、截斷至 `MAX_SEARCH_Q_LEN`；
      - 日期：只接受 `YYYY-MM-DD`，格式錯誤視為未填；
      - 起訖顛倒時自動對調，避免使用者因輸入順序拿到永遠空白的結果。

    `args` 可以是 Flask `request.args` 或一般 dict；本函式不依賴 Flask。
    """
    def _date(raw):
        try:
            return parse_date(str(raw or '').strip()).isoformat()
        except (ValueError, TypeError):
            return ''

    keyword = str(args.get('q') or '').strip()[:MAX_SEARCH_Q_LEN]
    date_from = _date(args.get('date_from'))
    date_to = _date(args.get('date_to'))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    return {'q': keyword, 'date_from': date_from, 'date_to': date_to}


def like_keyword(keyword):
    """把使用者輸入轉成安全的 `LIKE` 樣式（`%`／`_`／`!` 皆跳脫）。

    以 `ESCAPE '!'` 搭配：不跳脫的話，輸入 `%` 會撈出全部資料、
    輸入底線會變成單字元萬用字元，看似搜尋其實是全表列舉。
    """
    escaped = (keyword.replace('!', '!!')
               .replace('%', '!%')
               .replace('_', '!_'))
    return f'%{escaped}%'


def search_conditions(filters, columns):
    """產生搜尋用的 WHERE 條件與參數。

    `columns` 只能是程式碼內寫死的欄位名稱（含資料表別名），不可由使用者
    傳入；關鍵字走參數化 `LIKE ? ESCAPE '!'`，日期區間採「請假區間與查詢
    區間有交集」語意。回傳 `(conditions, args)`。
    """
    conds = []
    args = []
    keyword = (filters or {}).get('q') or ''
    if keyword:
        pattern = like_keyword(keyword)
        conds.append('(' + ' OR '.join(
            f"COALESCE({col}, '') LIKE ? ESCAPE '!'" for col in columns) + ')')
        args.extend([pattern] * len(columns))
    date_from = (filters or {}).get('date_from') or ''
    date_to = (filters or {}).get('date_to') or ''
    if date_from:
        conds.append('date(r.end_date) >= date(?)')
        args.append(date_from)
    if date_to:
        conds.append('date(r.start_date) <= date(?)')
        args.append(date_to)
    return conds, args


def calc_days(start_date, end_date, start_period, end_period):
    """按半天粒度計算請假天數。"""
    if end_date < start_date:
        return 0
    full = (end_date - start_date).days - 1          # 中間的完整天
    head = 0.5 if start_period == 'PM' else 1.0
    tail = 0.5 if end_period == 'AM' else 1.0
    if start_date == end_date:
        if start_period == 'AM' and end_period == 'PM':
            return 1.0
        return 0.5
    return max(full, 0) + head + tail


def required_level_for(days, leave_type=None, needs_faculty=None):
    """回傳本單需審到第幾級。leave_type 可為 dict（含 needs_faculty）或直接傳 bool。"""
    if needs_faculty is None:
        needs_faculty = bool(leave_type and leave_type['needs_faculty'])
    if needs_faculty:
        return 2
    return 2 if days > FACULTY_THRESHOLD_DAYS else 1


def academic_year_start(today=None):
    """回傳當前學年的起始日（9/1）。"""
    today = today or date.today()
    year = today.year if today.month >= ACADEMIC_YEAR_START_MONTH else today.year - 1
    return date(year, ACADEMIC_YEAR_START_MONTH, 1)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


# 允許格式的檔案頭魔術位元組（magic bytes）：
# PDF／PNG／JPEG／OLE2（.doc）／ZIP（.docx）。副檔名可以任意改，內容頭改不了。
_MAGIC_SIGNATURES = (
    b'%PDF',              # PDF
    b'\x89PNG',           # PNG
    b'\xff\xd8\xff',      # JPEG
    b'\xd0\xcf\x11\xe0',  # OLE2 複合文件 → .doc
    b'PK\x03\x04',        # ZIP 容器 → .docx
)


def sniff_allowed_type(head):
    """副檔名說謊防護：依檔案頭魔術位元組判斷真實格式。

    head 為檔案開頭位元組（建議讀 8 位元組）。攔掉「打著 proof.pdf 檔名、
    實際上是可執行檔」之類的上傳——副檔名白名單只擋得了改名，擋不了換內容。
    """
    if not head:
        return False
    return any(head.startswith(sig) for sig in _MAGIC_SIGNATURES)


def valid_role(role):
    """角色白名單校驗：表單傳入的角色必須是四種合法角色之一。"""
    return role in VALID_ROLES


def valid_username(username):
    """帳號格式：1–50 字元，僅允許字母、數字與 . _ - @。"""
    return bool(_USERNAME_RE.fullmatch(username or ''))


def valid_type_code(code):
    """假別代號格式：1–20 字元，僅字母、數字與 - _。"""
    return bool(_TYPE_CODE_RE.fullmatch(code or ''))


_EMAIL_RE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


def valid_email(email):
    """Email 粗校驗：形式不合法就不寫入（通知通道依賴它）。"""
    return bool(_EMAIL_RE.fullmatch(email or ''))


# 聯絡電話：數字與 + ( ) - 空格；長度上限沿用 MAX_PHONE_LEN
_PHONE_RE = re.compile(r'[0-9+()\- ]{1,%d}' % MAX_PHONE_LEN)


def valid_phone(phone):
    """聯絡電話格式校驗（v1.1.0）。空值視為合法——電話非必填；
    填了就必須像支電話，擋掉「abc」「<script>」之類的雜訊。"""
    if not phone:
        return True
    return bool(_PHONE_RE.fullmatch(phone))


def validate_password(pw, username=None):
    """密碼強度策略：長度 8–128、不得與帳號相同、須含大寫、小寫與數字、
    不可為常見弱密碼。

    回傳錯誤訊息（字串）或 None。username 可選；提供時會一併檢查是否與帳號相同。
    """
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        return f'密碼長度至少需 {MIN_PASSWORD_LEN} 個字元。'
    if len(pw) > MAX_PASSWORD_LEN:
        return f'密碼長度不可超過 {MAX_PASSWORD_LEN} 個字元。'
    if pw.lower() in COMMON_PASSWORDS:
        return '此密碼過於常見（已被列入弱密碼擋鎖），請換一組。'
    if username and pw.strip().lower() == username.strip().lower():
        return '密碼不可與帳號相同。'
    if not (any(c.isupper() for c in pw)
            and any(c.islower() for c in pw)
            and any(c.isdigit() for c in pw)):
        return '密碼需同時包含大寫字母、小寫字母與數字。'
    return None


def temp_password():
    """產生一組必定通過 validate_password 的隨機臨時密碼。"""
    return f"Rst{secrets.token_hex(5)}9"


# CSV 公式注入：以這些字元開頭的儲存格在 Excel / Sheets 會被當公式執行
_CSV_DANGER_PREFIXES = ('=', '+', '-', '@')


def csv_safe(value):
    """CSV 公式注入防護：= + - @ 開頭的字串加 ' 前綴（試算表視為純文字）。

    非字串或安全開頭的值原樣回傳。
    """
    if isinstance(value, str) and value[:1] in _CSV_DANGER_PREFIXES:
        return "'" + value
    return value
