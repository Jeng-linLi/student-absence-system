-- =====================================================================
-- SAAS — Student Absence & Academic Accommodation System
-- Student Absence & Academic Accommodation System · 學生請假與學業便利支援系統
-- PostgreSQL 15 · Azure Database for PostgreSQL Flexible Server
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";       -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";         -- case-insensitive email/login

-- ---------- 枚舉類型 -------------------------------------------------
CREATE TYPE user_role AS ENUM (
  'student','instructor','program_office','registry','student_affairs',
  'sports_coordinator','event_organizer','advisor','admin'
);

CREATE TYPE absence_category AS ENUM (
  'medical','university_activity','sports_competition','conference',
  'family_emergency','visa_immigration','bereavement','personal','other'
);

CREATE TYPE request_status AS ENUM (
  'draft','pending','info_requested','approved','rejected','withdrawn','expired'
);

CREATE TYPE conflict_type AS ENUM (
  'quiz','midterm','final','workshop','laboratory'
);

CREATE TYPE accommodation_type AS ENUM (
  'excused','make_up','alternative_assignment','weight_redistribution','rejected'
);

CREATE TYPE approval_action AS ENUM ('approve','reject','request_info','delegate','auto_verify');

CREATE TYPE notification_channel AS ENUM ('email','push','sms','teams','wechat','in_app');

-- =====================================================================
-- 1. 組織與人員
-- =====================================================================
CREATE TABLE departments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(20)  NOT NULL UNIQUE,          -- e.g. AI, DS, ROAS
    name_en     VARCHAR(200) NOT NULL,
    name_zh     VARCHAR(200) NOT NULL,
    parent_id   UUID REFERENCES departments(id),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entra_oid     VARCHAR(64) UNIQUE,                  -- Entra ID object id (SSO)
    username      CITEXT NOT NULL UNIQUE,              -- student / staff number
    email         CITEXT NOT NULL UNIQUE,
    display_name  VARCHAR(120) NOT NULL,
    display_name_zh VARCHAR(120),
    primary_role  user_role NOT NULL,
    department_id UUID REFERENCES departments(id),
    locale        VARCHAR(8) NOT NULL DEFAULT 'zh-CN',
    timezone      VARCHAR(48) NOT NULL DEFAULT 'Asia/Shanghai',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN users.entra_oid IS 'Microsoft Entra ID object identifier; null only for break-glass local admin';

-- 一人可同時具備多重角色（例如教師兼任導師）
CREATE TABLE user_roles (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        user_role NOT NULL,
    scope_type  VARCHAR(20) NOT NULL DEFAULT 'all',   -- all|department|programme|course
    scope_id    UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'::uuid,
    granted_by  UUID REFERENCES users(id),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role, scope_type, scope_id)
);

CREATE TABLE students (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    student_no      CITEXT NOT NULL UNIQUE,
    programme_code  VARCHAR(20) NOT NULL,
    cohort          VARCHAR(10) NOT NULL,              -- e.g. 2024
    advisor_id      UUID REFERENCES users(id),         -- academic advisor
    enrollment_status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE faculty (
    user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    staff_no      CITEXT NOT NULL UNIQUE,
    title         VARCHAR(60),
    office_location VARCHAR(80),
    can_approve   BOOLEAN NOT NULL DEFAULT TRUE,
    delegate_id   UUID REFERENCES users(id),           -- SLA 升級代理人
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- 2. 課程與修讀
-- =====================================================================
CREATE TABLE courses (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code          VARCHAR(20)  NOT NULL,               -- AIAA 1010
    term          VARCHAR(12)  NOT NULL,               -- 2026-27 Fall
    section       VARCHAR(8)   NOT NULL DEFAULT '001',
    title_en      VARCHAR(200) NOT NULL,
    title_zh      VARCHAR(200),
    department_id UUID REFERENCES departments(id),
    instructor_id UUID REFERENCES users(id),
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code, term, section)
);

CREATE TABLE enrollments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id   UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    status      VARCHAR(16) NOT NULL DEFAULT 'enrolled',  -- enrolled|dropped|completed
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, course_id)
);

-- 考評行事曆（夜間自 SIS / Canvas 同步）
CREATE TABLE assessments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id    UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title        VARCHAR(200) NOT NULL,
    type         conflict_type NOT NULL,
    assessment_date DATE NOT NULL,
    start_time   TIME,
    end_time     TIME,
    venue        VARCHAR(80),
    weight_pct   NUMERIC(5,2),
    mandatory    BOOLEAN NOT NULL DEFAULT TRUE,
    source       VARCHAR(16) NOT NULL DEFAULT 'sis',   -- sis|canvas|manual
    external_id  VARCHAR(80),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_id, external_id)
);

-- =====================================================================
-- 3. 請假申請
-- =====================================================================
CREATE TABLE absence_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_no     VARCHAR(20) NOT NULL UNIQUE,      -- SAAS-2026-001234 (human readable)
    student_id       UUID NOT NULL REFERENCES users(id),
    category         absence_category NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    start_period     CHAR(2) NOT NULL DEFAULT 'AM',    -- AM|PM
    end_period       CHAR(2) NOT NULL DEFAULT 'PM',
    days             NUMERIC(4,1) NOT NULL,            -- 半天計 0.5
    reason           TEXT NOT NULL,
    contact_phone    VARCHAR(32),
    status           request_status NOT NULL DEFAULT 'pending',
    route_code       VARCHAR(20) NOT NULL,             -- A-MED / B-OFFICIAL / C-LONG …
    route_version    INTEGER NOT NULL DEFAULT 1,
    current_level    INTEGER NOT NULL DEFAULT 1,
    required_levels  INTEGER NOT NULL DEFAULT 1,
    auto_verified    BOOLEAN NOT NULL DEFAULT FALSE,
    activity_id      UUID,            -- FK 於 events 建立後加上（見 §4）
    sla_due_at       TIMESTAMPTZ,
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_date_range CHECK (end_date >= start_date),
    CONSTRAINT chk_days_positive CHECK (days > 0),
    CONSTRAINT chk_activity_required CHECK (
      (category IN ('university_activity','sports_competition') AND activity_id IS NOT NULL)
      OR category NOT IN ('university_activity','sports_competition')
    )
);
COMMENT ON COLUMN absence_requests.reference_no IS 'Human-readable reference used in correspondence and reports';

CREATE TABLE documents (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id     UUID NOT NULL REFERENCES absence_requests(id) ON DELETE CASCADE,
    uploaded_by    UUID NOT NULL REFERENCES users(id),
    file_name      VARCHAR(255) NOT NULL,
    blob_path      VARCHAR(512) NOT NULL,              -- Azure Blob path
    content_type   VARCHAR(100) NOT NULL,
    size_bytes     INTEGER NOT NULL,
    sha256         CHAR(64) NOT NULL,                  -- 完整性驗證 / 防重複
    scan_status    VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|clean|infected
    is_medical     BOOLEAN NOT NULL DEFAULT FALSE,     -- 觸發嚴格存取控制
    retention_until DATE,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_size CHECK (size_bytes > 0 AND size_bytes <= 10485760)
);

CREATE TABLE approvals (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id     UUID NOT NULL REFERENCES absence_requests(id) ON DELETE CASCADE,
    level          INTEGER NOT NULL,
    approver_role  user_role NOT NULL,
    approver_id    UUID REFERENCES users(id),
    action         approval_action,
    comment        TEXT,
    conditions     TEXT,                               -- Registry 附加條件（C 案）
    sla_hours      INTEGER NOT NULL,
    due_at         TIMESTAMPTZ,
    acted_at       TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (request_id, level)
);

-- =====================================================================
-- 4. 官方活動
-- =====================================================================
CREATE TABLE events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(200) NOT NULL,
    category      VARCHAR(30) NOT NULL,                -- sports|exchange|research|conference|other
    organising_unit VARCHAR(120) NOT NULL,
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,
    created_by    UUID NOT NULL REFERENCES users(id),
    endorsed_by   UUID REFERENCES users(id),
    endorsed_at   TIMESTAMPTZ,
    status        VARCHAR(16) NOT NULL DEFAULT 'draft',  -- draft|endorsed|archived
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_event_dates CHECK (end_date >= start_date)
);

-- 回填外鍵：absence_requests.activity_id → events.id
ALTER TABLE absence_requests
    ADD CONSTRAINT fk_req_activity FOREIGN KEY (activity_id) REFERENCES events(id);

CREATE TABLE event_participants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id     UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    student_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),   -- 快照時間：防止事後加名單追溯核實
    added_by     UUID NOT NULL REFERENCES users(id),
    source       VARCHAR(16) NOT NULL DEFAULT 'roster_upload',
    UNIQUE (event_id, student_id)
);

-- =====================================================================
-- 5. 考評衝突與便利安排
-- =====================================================================
CREATE TABLE assessment_conflicts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID NOT NULL REFERENCES absence_requests(id) ON DELETE CASCADE,
    course_id       UUID NOT NULL REFERENCES courses(id),
    assessment_id   UUID REFERENCES assessments(id),
    conflict_type   conflict_type NOT NULL,
    conflict_date   DATE NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    instructor_id   UUID NOT NULL REFERENCES users(id),
    resolved_at     TIMESTAMPTZ
);
-- 同一申請下，同一課程的同一考評只產生一次衝突（assessment_id 可為空）
CREATE UNIQUE INDEX uq_conflict ON assessment_conflicts
    (request_id, course_id, COALESCE(assessment_id, '00000000-0000-0000-0000-000000000000'::uuid));

CREATE TABLE accommodations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conflict_id       UUID NOT NULL REFERENCES assessment_conflicts(id) ON DELETE CASCADE,
    type              accommodation_type NOT NULL,
    decided_by        UUID NOT NULL REFERENCES users(id),
    new_deadline      DATE,                            -- alternative_assignment
    make_up_date      DATE,
    make_up_venue     VARCHAR(80),
    make_up_mode      VARCHAR(16),                     -- in_person|online|to_be_confirmed
    weight_before_pct NUMERIC(5,2),
    weight_after_pct  NUMERIC(5,2),
    rejection_reason  TEXT,
    comment           TEXT,
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_reject_reason CHECK (type <> 'rejected' OR rejection_reason IS NOT NULL)
);

-- =====================================================================
-- 6. 通知、預警、審計
-- =====================================================================
CREATE TABLE notifications (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_id    UUID REFERENCES absence_requests(id) ON DELETE CASCADE,
    event_type    VARCHAR(40) NOT NULL,
    channel       notification_channel NOT NULL,
    payload_json  JSONB NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'queued',  -- queued|sent|failed|read
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ,
    read_at       TIMESTAMPTZ
);

CREATE TABLE notification_preferences (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type  VARCHAR(40) NOT NULL,
    channel     notification_channel NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (user_id, event_type, channel)
);

CREATE TABLE risk_alerts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rule_code     VARCHAR(16) NOT NULL,                -- EW-1 … EW-5
    severity      VARCHAR(12) NOT NULL,                -- low|medium|high
    detail_json   JSONB NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'open', -- open|assigned|closed
    assigned_to   UUID REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ
);

-- 僅可追加（append-only）＋雜湊鏈，防竄改
CREATE TABLE audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    entity_type    VARCHAR(40) NOT NULL,
    entity_id      UUID NOT NULL,
    actor_id       UUID REFERENCES users(id),
    actor_role     user_role,
    action         VARCHAR(60) NOT NULL,
    before_json    JSONB,
    after_json     JSONB,
    ip_address     INET,
    user_agent     TEXT,
    correlation_id UUID NOT NULL,
    prev_hash      CHAR(64),
    row_hash       CHAR(64) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- 7. 政策配置（毋須發版即可調整）
-- =====================================================================
CREATE TABLE policy_config (
    key          VARCHAR(60) PRIMARY KEY,
    value_json   JSONB NOT NULL,
    description  TEXT,
    updated_by   UUID REFERENCES users(id),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO policy_config (key, value_json, description) VALUES
  ('faculty_threshold_days', '3',      '病假超過此天數需教務處會簽 / Medical leave days requiring Registry countersign'),
  ('long_absence_days',      '7',      '觸發 C 案（項目辦公室 → 教務處 → 教師）的天數 / Days triggering Case C'),
  ('sla.instructor_hours',   '24',     '教師步驟 SLA'),
  ('sla.registry_hours',     '48',     '教務處步驟 SLA'),
  ('sla.escalation_pct',     '100',    '達 SLA 百分比即升級'),
  ('warning.absence_pct',    '10',     '缺席率預警門檻 (%)'),
  ('warning.consecutive',    '3',      '連續缺席節次門檻'),
  ('warning.medical_count',  '3',      '單學期病假次數門檻'),
  ('route_version',          '1',      '當前路由規則版本');

-- =====================================================================
-- 8. 索引策略
-- =====================================================================
-- 高頻查詢：學生自己的申請（最新優先）
CREATE INDEX idx_req_student_created   ON absence_requests (student_id, created_at DESC);
-- 待辦佇列：按狀態 + 當前層級 + SLA 到期時間
CREATE INDEX idx_req_status_level_due  ON absence_requests (status, current_level, sla_due_at)
    WHERE status IN ('pending','info_requested');
-- 教師待辦：衝突表按教師 + 未處理
CREATE INDEX idx_conflict_instructor_open ON assessment_conflicts (instructor_id, resolved_at)
    WHERE resolved_at IS NULL;
CREATE INDEX idx_conflict_request      ON assessment_conflicts (request_id);
-- 審批步驟
CREATE INDEX idx_approval_request      ON approvals (request_id, level);
CREATE INDEX idx_approval_approver     ON approvals (approver_id, action) WHERE acted_at IS NULL;
-- 活動名單核實
CREATE INDEX idx_participant_student   ON event_participants (student_id, event_id);
CREATE INDEX idx_event_dates           ON events (start_date, end_date) WHERE status = 'endorsed';
-- 修讀與考評
CREATE INDEX idx_enrollment_student    ON enrollments (student_id) WHERE status = 'enrolled';
CREATE INDEX idx_enrollment_course     ON enrollments (course_id);
CREATE INDEX idx_assessment_course_dt  ON assessments (course_id, assessment_date);
-- 文件
CREATE INDEX idx_document_request      ON documents (request_id);
CREATE INDEX idx_document_sha          ON documents (sha256);       -- 重複/偽造偵測
-- 通知
CREATE INDEX idx_notif_user_unread     ON notifications (user_id, status) WHERE status IN ('queued','sent');
CREATE INDEX idx_notif_retry           ON notifications (status, created_at) WHERE status = 'failed';
-- 預警與審計
CREATE INDEX idx_risk_open             ON risk_alerts (status, severity, created_at DESC) WHERE status <> 'closed';
CREATE INDEX idx_audit_entity          ON audit_logs (entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_actor           ON audit_logs (actor_id, created_at DESC);
CREATE INDEX idx_audit_correlation     ON audit_logs (correlation_id);
-- 全文檢索（P2）
CREATE INDEX idx_req_reason_fts        ON absence_requests USING gin (to_tsvector('simple', reason));

-- =====================================================================
-- 9. 觸發器：updated_at
-- =====================================================================
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated    BEFORE UPDATE ON users             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_requests_updated BEFORE UPDATE ON absence_requests  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- 10. 列級安全（RLS）
-- =====================================================================
ALTER TABLE absence_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE approvals        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs       ENABLE ROW LEVEL SECURITY;

-- 學生：只見自己的申請
CREATE POLICY p_student_own_requests ON absence_requests
    FOR SELECT USING (
        student_id = current_setting('saas.user_id', true)::uuid
        OR current_setting('saas.role', true) IN
           ('registry','program_office','student_affairs','admin')
    );

-- 教師：只見所授課程相關的申請（經衝突表關聯）
CREATE POLICY p_instructor_course_requests ON absence_requests
    FOR SELECT USING (
        current_setting('saas.role', true) <> 'instructor'
        OR EXISTS (
            SELECT 1 FROM assessment_conflicts ac
            WHERE ac.request_id = absence_requests.id
              AND ac.instructor_id = current_setting('saas.user_id', true)::uuid
        )
    );

-- 醫療文件：僅教務處、學生事務處、導師、本人
CREATE POLICY p_medical_documents ON documents
    FOR SELECT USING (
        is_medical = FALSE
        OR uploaded_by = current_setting('saas.user_id', true)::uuid
        OR current_setting('saas.role', true) IN ('registry','student_affairs','admin')
    );

-- 審計日誌：僅管理員與教務處可讀，任何人不可改
CREATE POLICY p_audit_read ON audit_logs
    FOR SELECT USING (current_setting('saas.role', true) IN ('admin','registry'));
CREATE POLICY p_audit_no_update ON audit_logs FOR UPDATE USING (false);
CREATE POLICY p_audit_no_delete ON audit_logs FOR DELETE USING (false);

-- =====================================================================
-- 11. 權限：應用程式角色最小權限
-- =====================================================================
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO saas_app;
-- REVOKE UPDATE, DELETE ON audit_logs FROM saas_app;   -- 僅可追加
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO saas_readonly;  -- analytics-svc 用

-- =====================================================================
-- 12. 資料保存與清除（對齊 FERPA / GDPR）
-- =====================================================================
-- 醫療證明：畢業後 7 年 → 自動歸檔至 Blob Archive，屆滿刪除
-- 一般請假紀錄：10 年
-- 通知遞送紀錄：12 個月
-- 審計日誌：永久保存（雜湊鏈）
-- Session / Redis：8 小時 TTL
