# -*- coding: utf-8 -*-
"""SQLite 連線與查詢輔助。"""
import os
import sqlite3
from contextlib import contextmanager

from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('LEAVE_DB') or os.path.join(BASE_DIR, 'leave_system.db')
UPLOAD_DIR = os.environ.get('LEAVE_UPLOAD_DIR') or os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_db():
    if 'db' not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        g.db = conn
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def _write(sql, args=()):
    """執行寫入；交易進行中不逐句 commit，交由 transaction() 統一提交。"""
    db = get_db()
    cur = db.execute(sql, args)
    if not getattr(g, '_tx_depth', 0):
        db.commit()
    return cur


def execute(sql, args=()):
    return _write(sql, args).lastrowid


def execute_guard(sql, args=()):
    """條件式更新：回傳影響列數。0 代表 WHERE 沒匹配上——呼叫端應視為
    「狀態已被他人改動」，不可假設寫入成功（v1.1.0 併發守衛的基礎）。"""
    cur = _write(sql, args)
    n = cur.rowcount
    cur.close()
    return n


@contextmanager
def transaction():
    """多寫入流程的原子區塊：期間 execute() 不再逐句 commit，
    全部成功才一次提交；中途拋出例外則整體回滾，不留半套狀態。

    用於審批（approvals＋leave_requests＋log＋notify 四寫）、
    撤回、提交假單這類「要麼全部生效、要麼全部沒發生」的流程。
    巢狀區塊沿用外層交易，提交／回滾交由最外層負責。
    """
    db = get_db()
    depth = getattr(g, '_tx_depth', 0)
    g._tx_depth = depth + 1
    try:
        yield db
        if depth == 0:
            db.commit()
    except Exception:
        if depth == 0:
            db.rollback()
        raise
    finally:
        g._tx_depth = depth


def init_db():
    with open(os.path.join(BASE_DIR, 'schema.sql'), encoding='utf-8') as f:
        get_db().executescript(f.read())
    get_db().commit()
    apply_migrations()


# 向後相容遷移：舊版資料庫缺少的欄位列於此，啟動時自動補上（不影響既有資料）。
# 全新庫已由 schema.sql 直接建立，這裡只對歷史庫生效。
MIGRATIONS = [
    ('leave_requests', 'affected_courses', "TEXT NOT NULL DEFAULT ''"),
    ('users', 'pwd_changed_at', 'TEXT'),
    ('timetable', 'source', "TEXT NOT NULL DEFAULT 'demo'"),
    ('users', 'external_id', 'TEXT'),   # A2 SSO（v1.8.0）：外部身分對映，本地帳號為 NULL
    ('users', 'email_verified', 'INTEGER NOT NULL DEFAULT 0'),   # B2（v1.12.0）：Email 驗證標記
]

# 補建資料表（v1.12.0）：MIGRATIONS 只補欄位，整表新增走這裡（冪等）。
EXTRA_TABLES = [
    """CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT    NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    purpose    TEXT    NOT NULL DEFAULT 'reset' CHECK (purpose IN ('reset','verify')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at TEXT    NOT NULL,
    used_at    TEXT
)""",
]

# 補建索引（v1.7.0）：課表匯入的 merge 模式靠唯一索引做冪等 UPSERT。
# 舊庫若已經有重複資料，索引建不起來——此時不該讓啟動失敗，
# 匯入會自動退回「先刪同鍵列再寫入」（見 timetable_import._insert_row）。
EXTRA_INDEXES = [
    ('uq_tt_slot',
     'CREATE UNIQUE INDEX IF NOT EXISTS uq_tt_slot '
     'ON timetable(student_id, weekday, start_time, end_time, course_code)'),
]


def apply_migrations():
    """依 MIGRATIONS 清單為既存資料表補齊缺少的欄位（冪等）。"""
    for table, column, ddl in MIGRATIONS:
        cols = [r['name'] for r in q(f'PRAGMA table_info({table})')]
        if column not in cols:
            execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
    for ddl in EXTRA_TABLES:
        execute(ddl)
    for name, ddl in EXTRA_INDEXES:
        if not q("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                 (name,), one=True):
            try:
                execute(ddl)
            except Exception:            # noqa: BLE001 — 建不出索引不阻擋啟動
                print(f'[遷移警告] 索引 {name} 建立失敗（可能有重複資料），'
                      '課表匯入將改用去重後寫入。')
