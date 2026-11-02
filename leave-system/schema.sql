-- 線上請假系統 · 資料庫結構（SQLite）
PRAGMA foreign_keys = ON;

-- 用戶：學生 / 導師 / 院系審批人 / 管理員
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,          -- 登入帳號（學號或教工號）
    password_hash TEXT    NOT NULL,
    pwd_changed_at TEXT,                            -- 密碼最後變更時間；登入早於此時間的 session 作廢
    name          TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('student','advisor','faculty','admin')),
    department    TEXT    NOT NULL DEFAULT '',      -- 所屬院系
    class_name    TEXT    NOT NULL DEFAULT '',      -- 班級（學生）
    email         TEXT    NOT NULL DEFAULT '',
    external_id   TEXT,                              -- SSO / 外部身分對映（A2，v1.8.0）；本地帳號為 NULL
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
    affected_courses TEXT  NOT NULL DEFAULT '',     -- 依課表算出的受影響課程（JSON）
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

-- 登入限流紀錄。持久化到資料庫：跨進程、重啟後仍有效
-- （僅存進程內記憶體的話，多 worker 部署下限流形同虛設）
CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT    NOT NULL,                          -- 來源 IP + 帳號
    ts  REAL    NOT NULL                           -- Unix timestamp
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_key ON login_attempts(key, ts);

-- 課表（依學生每週上課時間，用於推算請假受影響課程）
-- v1.7.0：逐生課表不再是「所有人共用一份示範資料」——source 標記課表來源，
-- 讓管理員一眼看出哪些學生的課表還是假的。
CREATE TABLE IF NOT EXISTS timetable (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id),
    weekday     INTEGER NOT NULL,                 -- 0=週一 .. 4=週五
    start_time  TEXT    NOT NULL,                 -- HH:MM（24 小時制）
    end_time    TEXT    NOT NULL,
    course_code TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT '',      -- Lecture / Tutorial / Lab
    room        TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'demo',  -- demo / import / manual
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_tt_student ON timetable(student_id, weekday);
-- 唯一索引：同一學生、同一時段、同一門課只會有一列。
-- 課表匯入（merge 模式）靠它做冪等 UPSERT，重跑匯入不會長出重複的課。
CREATE UNIQUE INDEX IF NOT EXISTS uq_tt_slot
    ON timetable(student_id, weekday, start_time, end_time, course_code);

-- v1.4.0 · 委託審批（B4）：審批人不在時把該級的審批權暫時交給代理人。
-- 只在 [start_date, end_date] 內生效；delegator 的角色決定可代審的範圍
-- （導師 → 自己名下學生；院系教師 → 自己院系的假單）。
CREATE TABLE IF NOT EXISTS delegations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    delegator_id INTEGER NOT NULL REFERENCES users(id),   -- 委託人（原審批人）
    delegate_id  INTEGER NOT NULL REFERENCES users(id),   -- 代理人
    level        INTEGER NOT NULL CHECK (level IN (1, 2)),
    start_date   TEXT    NOT NULL,                        -- YYYY-MM-DD（含）
    end_date     TEXT    NOT NULL,                        -- YYYY-MM-DD（含）
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (delegator_id, delegate_id, level, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_deleg_delegate ON delegations(delegate_id, level);
CREATE INDEX IF NOT EXISTS idx_deleg_delegator ON delegations(delegator_id, level);

-- v1.4.0 · 學期（C2）：dashboard / stats / 額度都按學期切。
CREATE TABLE IF NOT EXISTS terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT    NOT NULL UNIQUE,           -- 例：2026-27 秋季
    start_date TEXT    NOT NULL,
    end_date   TEXT    NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,        -- 同時只應有一個 1
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- v1.4.0 · 請假額度（B3 / C2）：某學期、某假別的累計天數上限。
-- scope 預留給未來「按院系／班級」設定，目前一律 'all'。
CREATE TABLE IF NOT EXISTS leave_quotas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id       INTEGER NOT NULL REFERENCES terms(id),
    leave_type_id INTEGER NOT NULL REFERENCES leave_types(id),
    max_days      REAL    NOT NULL,
    scope         TEXT    NOT NULL DEFAULT 'all',
    scope_value   TEXT    NOT NULL DEFAULT '',
    UNIQUE (term_id, leave_type_id, scope, scope_value)
);

-- v1.4.0 · 校曆（C3）：非上課日（公眾假期、校曆假期）。
-- affected_courses_for() 遇到這些日期不再列出受影響課程——
-- 假日請假本來就不會缺課，列出來只會讓審批人誤判。
CREATE TABLE IF NOT EXISTS holidays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT    NOT NULL UNIQUE,           -- YYYY-MM-DD
    label      TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- v1.5.0 · 補課 / 調課日（C3）：週末（或假日）要補平日的課。
-- 校曆上常見「10/10（六）補星期五的課」——這天雖然是週六，學生請假
-- 照樣會缺課，所以 is_school_off() 要為它開例外，而且受影響課程要按
-- 「被補的那個星期幾」的課表算，不是按週六算（週六本來就沒課）。
CREATE TABLE IF NOT EXISTS makeup_days (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT    NOT NULL UNIQUE,        -- 補課日 YYYY-MM-DD
    cover_weekday INTEGER NOT NULL,               -- 補星期幾的課：0=週一 .. 4=週五
    label         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
