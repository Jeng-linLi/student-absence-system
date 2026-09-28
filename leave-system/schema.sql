-- 線上請假系統 · 資料庫結構（SQLite）
PRAGMA foreign_keys = ON;

-- 用戶：學生 / 導師 / 院系審批人 / 管理員
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,          -- 登入帳號（學號或教工號）
    password_hash TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('student','advisor','faculty','admin')),
    department    TEXT    NOT NULL DEFAULT '',      -- 所屬院系
    class_name    TEXT    NOT NULL DEFAULT '',      -- 班級（學生）
    email         TEXT    NOT NULL DEFAULT '',
    advisor_id    INTEGER REFERENCES users(id),     -- 學生的導師
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 假別
CREATE TABLE IF NOT EXISTS leave_types (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    code                TEXT    NOT NULL UNIQUE,
    name                TEXT    NOT NULL,
    requires_attachment INTEGER NOT NULL DEFAULT 0, -- 是否必須上傳證明
    needs_faculty       INTEGER NOT NULL DEFAULT 0, -- 是否無論天數都需院系審批
    active              INTEGER NOT NULL DEFAULT 1
);

-- 假單
CREATE TABLE IF NOT EXISTS leave_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id     INTEGER NOT NULL REFERENCES users(id),
    leave_type_id  INTEGER NOT NULL REFERENCES leave_types(id),
    start_date     TEXT    NOT NULL,                -- YYYY-MM-DD
    end_date       TEXT    NOT NULL,
    start_period   TEXT    NOT NULL DEFAULT 'AM',   -- AM / PM
    end_period     TEXT    NOT NULL DEFAULT 'PM',
    days           REAL    NOT NULL,                -- 請假天數（半天計 0.5）
    reason         TEXT    NOT NULL,
    attachment     TEXT    NOT NULL DEFAULT '',     -- 證明文件檔名
    contact_phone  TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected','withdrawn')),
    current_level  INTEGER NOT NULL DEFAULT 1,      -- 1=待導師  2=待院系
    required_level INTEGER NOT NULL DEFAULT 1,      -- 本單需要審到第幾級
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_req_student ON leave_requests(student_id);
CREATE INDEX IF NOT EXISTS idx_req_status  ON leave_requests(status, current_level);

-- 審批記錄（每一級一條）
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL REFERENCES leave_requests(id),
    level       INTEGER NOT NULL,                   -- 1=導師 2=院系
    approver_id INTEGER REFERENCES users(id),
    action      TEXT    NOT NULL CHECK (action IN ('approve','reject','pending')),
    comment     TEXT    NOT NULL DEFAULT '',
    acted_at    TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_app_req ON approvals(request_id);

-- 操作日誌
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER REFERENCES leave_requests(id),
    actor_id   INTEGER REFERENCES users(id),
    action     TEXT    NOT NULL,
    detail     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 站內通知
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    request_id INTEGER REFERENCES leave_requests(id),
    message    TEXT    NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
