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

# 自由文字長度上限（防止超長內容撐爆版面／儲存）
MAX_REASON_LEN = 500
MAX_COMMENT_LEN = 300
MAX_PHONE_LEN = 30
MIN_PASSWORD_LEN = 8

# 登入限流
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SEC = 300

# 學年起始月份（9 月），用於統計「本學年已核准天數」
ACADEMIC_YEAR_START_MONTH = 9

# 原型版本號（模板頁腳從此帶入，避免各處硬編碼後失真）
APP_VERSION = '0.9.1'

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

# 閒置 session 逾時（分鐘）：超過此時間無操作即需重新登入。
# session 是簽名 cookie，服務端無法主動逐出，只能以時間戳記在每次請求時檢驗。
SESSION_IDLE_MINUTES = int(os.environ.get('LEAVE_SESSION_IDLE_MINUTES', '480'))


def parse_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


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


def validate_password(pw, username=None):
    """密碼強度策略：長度 ≥ 8、不得與帳號相同、須含大寫、小寫與數字、
    不可為常見弱密碼。

    回傳錯誤訊息（字串）或 None。username 可選；提供時會一併檢查是否與帳號相同。
    """
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        return f'密碼長度至少需 {MIN_PASSWORD_LEN} 個字元。'
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
