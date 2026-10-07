# -*- coding: utf-8 -*-
"""SQLite 連線與查詢輔助。"""
import os
import sqlite3

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


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def init_db():
    with open(os.path.join(BASE_DIR, 'schema.sql'), encoding='utf-8') as f:
        get_db().executescript(f.read())
    get_db().commit()
