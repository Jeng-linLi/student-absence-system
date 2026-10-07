# -*- coding: utf-8 -*-
"""純函式與常數：不依賴 Flask / 資料庫，便於單元測試。"""
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

# 登入限流
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SEC = 300

# 學年起始月份（9 月），用於統計「本學年已核准天數」
ACADEMIC_YEAR_START_MONTH = 9


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
