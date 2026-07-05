-- Autonomous Personal Agent System — PostgreSQL Schema
-- This file is mounted into the postgres container and executed on first start.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Core task record
CREATE TABLE tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_input    TEXT NOT NULL,
    intent       VARCHAR(50),
    risk_level   VARCHAR(20),
    status       VARCHAR(30) NOT NULL DEFAULT 'pending',
    entities     JSONB DEFAULT '{}',
    constraints  JSONB DEFAULT '[]',
    ambiguities  JSONB DEFAULT '[]',
    sub_tasks    JSONB DEFAULT '[]',
    result       JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    user_id      VARCHAR(100) DEFAULT 'default'
);

-- Immutable audit trail — append only, enforce at application AND trigger level
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    task_id        UUID REFERENCES tasks(id),
    sub_task_id    VARCHAR(50),
    agent_name     VARCHAR(100) NOT NULL,
    action         VARCHAR(200) NOT NULL,
    action_detail  JSONB DEFAULT '{}',
    risk_level     VARCHAR(20),
    result         VARCHAR(20),   -- success | failure | blocked | approved | rejected
    result_detail  JSONB DEFAULT '{}',
    screenshot_path TEXT,
    reasoning      TEXT,          -- LLM's stated reasoning for this action
    timestamp      TIMESTAMPTZ DEFAULT NOW()
);

-- Prevent any modification to audit records
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only. UPDATE and DELETE are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_immutability
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- Human approval requests
CREATE TABLE approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID REFERENCES tasks(id),
    sub_task_id     VARCHAR(50),
    action          VARCHAR(200) NOT NULL,
    context         JSONB NOT NULL,
    summary         TEXT NOT NULL,
    screenshot_path TEXT,
    risk_level      VARCHAR(20),
    status          VARCHAR(20) DEFAULT 'pending',  -- pending|approved|rejected|modified|timed_out
    modification    TEXT,
    requested_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     VARCHAR(100),
    timeout_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 minutes'
);

-- Long-term memory (structured metadata; actual vectors in ChromaDB)
CREATE TABLE memories (
    id               BIGSERIAL PRIMARY KEY,
    chroma_id        VARCHAR(100) UNIQUE,
    site             VARCHAR(200),
    memory_type      VARCHAR(50),  -- navigation|login_pattern|data_location|failure|preference
    content          TEXT NOT NULL,
    metadata         JSONB DEFAULT '{}',
    usefulness_score FLOAT DEFAULT 1.0,
    times_recalled   INT DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    last_recalled_at TIMESTAMPTZ
);

-- User preferences
CREATE TABLE user_preferences (
    id         BIGSERIAL PRIMARY KEY,
    key        VARCHAR(100) UNIQUE NOT NULL,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_tasks_status       ON tasks(status);
CREATE INDEX idx_tasks_created      ON tasks(created_at DESC);
CREATE INDEX idx_tasks_user         ON tasks(user_id);
CREATE INDEX idx_audit_task         ON audit_log(task_id);
CREATE INDEX idx_audit_timestamp    ON audit_log(timestamp DESC);
CREATE INDEX idx_approvals_status   ON approvals(status);
CREATE INDEX idx_approvals_timeout  ON approvals(timeout_at) WHERE status = 'pending';
CREATE INDEX idx_memories_site      ON memories(site);
CREATE INDEX idx_memories_type      ON memories(memory_type);

-- Default preferences
INSERT INTO user_preferences (key, value) VALUES
    ('currency_format',       'INR'),
    ('number_locale',         'en-IN'),
    ('report_format',         'markdown'),
    ('notification_channel',  'chainlit'),
    ('approval_timeout_mins', '30'),
    ('parallel_browsers_max', '3');
