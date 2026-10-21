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

# 角色白名單（與 schema.sql 的 CHECK 約束一致）
VALID_ROLES = ('student', 'advisor', 'faculty', 'admin')
_USERNAME_RE = re.compile(r'[A-Za-z0-9._@-]{1,50}')

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


def valid_role(role):
    """角色白名單校驗：表單傳入的角色必須是四種合法角色之一。"""
    return role in VALID_ROLES


def valid_username(username):
    """帳號格式：1–50 字元，僅允許字母、數字與 . _ - @。"""
    return bool(_USERNAME_RE.fullmatch(username or ''))


def validate_password(pw, username=None):
    """密碼強度策略：長度 ≥ 8、不得與帳號相同、須含大寫、小寫與數字。

    回傳錯誤訊息（字串）或 None。username 可選；提供時會一併檢查是否與帳號相同。
    """
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        return f'密碼長度至少需 {MIN_PASSWORD_LEN} 個字元。'
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
