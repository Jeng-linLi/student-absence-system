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
]


def apply_migrations():
    """依 MIGRATIONS 清單為既存資料表補齊缺少的欄位（冪等）。"""
    for table, column, ddl in MIGRATIONS:
        cols = [r['name'] for r in q(f'PRAGMA table_info({table})')]
        if column not in cols:
            execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
