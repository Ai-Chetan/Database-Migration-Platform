-- ============================================================
-- Connector Framework + CDC Engine
-- File: migration/backend/enterprise/db_migrations/005_connectors_cdc.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 005_connectors_cdc.sql
-- ============================================================


-- ── 1. Connector Registry ─────────────────────────────────────────────────────
-- Stores registered connector plugins with their capabilities.
-- When a new connector is added (e.g. Snowflake), it registers here.

CREATE TABLE IF NOT EXISTS connector_registry (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(100) UNIQUE NOT NULL,  -- "mysql", "postgresql", "snowflake"
    display_name        VARCHAR(255) NOT NULL,
    version             VARCHAR(50),
    db_type             VARCHAR(50) NOT NULL,
    capabilities        JSONB NOT NULL DEFAULT '[]',
    -- ["discover","stream_read","bulk_write","cdc","checksum","constraints"]
    supported_versions  JSONB DEFAULT '[]',            -- ["5.7","8.0"]
    config_schema       JSONB DEFAULT '{}',            -- JSON Schema for connection params
    is_active           BOOLEAN DEFAULT TRUE,
    is_builtin          BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Seed built-in connectors
INSERT INTO connector_registry
    (name, display_name, version, db_type, capabilities, supported_versions, is_builtin)
VALUES
('mysql',
 'MySQL',
 '1.0.0',
 'mysql',
 '["discover","stream_read","bulk_write","cdc","checksum","constraints","indexes"]'::jsonb,
 '["5.6","5.7","8.0","8.1"]'::jsonb,
 TRUE),

('postgresql',
 'PostgreSQL',
 '1.0.0',
 'postgresql',
 '["discover","stream_read","bulk_write","cdc","checksum","constraints","indexes","jsonb"]'::jsonb,
 '["11","12","13","14","15","16"]'::jsonb,
 TRUE),

('mariadb',
 'MariaDB',
 '1.0.0',
 'mariadb',
 '["discover","stream_read","bulk_write","cdc","checksum","constraints","indexes"]'::jsonb,
 '["10.4","10.5","10.6","10.11"]'::jsonb,
 TRUE),

('sqlite',
 'SQLite',
 '1.0.0',
 'sqlite',
 '["discover","stream_read","bulk_write","checksum"]'::jsonb,
 '["3.x"]'::jsonb,
 TRUE)

ON CONFLICT (name) DO NOTHING;


-- ── 2. CDC Sessions ───────────────────────────────────────────────────────────
-- One row per active CDC session tracking state for a job.

CREATE TABLE IF NOT EXISTS cdc_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    source_db_type      VARCHAR(50) NOT NULL,
    status              VARCHAR(50) DEFAULT 'initializing',
    -- initializing | capturing | replaying | paused | cutover_ready | completed | failed
    capture_method      VARCHAR(50),       -- binlog | wal | triggers | timestamp
    initial_load_done   BOOLEAN DEFAULT FALSE,
    initial_load_completed_at TIMESTAMP,
    capture_started_at  TIMESTAMP,
    last_captured_at    TIMESTAMP,
    last_replayed_at    TIMESTAMP,
    -- Position tracking (method-specific)
    binlog_file         VARCHAR(255),      -- MySQL: current binlog file
    binlog_position     BIGINT,            -- MySQL: current binlog position
    wal_lsn             VARCHAR(100),      -- PostgreSQL: WAL Log Sequence Number
    last_event_id       VARCHAR(255),      -- Generic: last processed event ID
    events_captured     BIGINT DEFAULT 0,
    events_replayed     BIGINT DEFAULT 0,
    events_pending      BIGINT DEFAULT 0,
    lag_seconds         INTEGER DEFAULT 0, -- replication lag
    error_message       TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cdc_sessions_job ON cdc_sessions (job_id);


-- ── 3. CDC Events ─────────────────────────────────────────────────────────────
-- Captured change events waiting to be replayed to target.
-- Uses JSONB for flexibility across source DB types.

CREATE TABLE IF NOT EXISTS cdc_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES cdc_sessions(id) ON DELETE CASCADE,
    event_type      VARCHAR(20) NOT NULL,  -- INSERT | UPDATE | DELETE | DDL
    table_name      VARCHAR(255) NOT NULL,
    event_position  VARCHAR(255),          -- binlog position or WAL LSN
    before_image    JSONB,                 -- row state before change (UPDATE/DELETE)
    after_image     JSONB,                 -- row state after change (INSERT/UPDATE)
    pk_values       JSONB,                 -- primary key values for this row
    replayed        BOOLEAN DEFAULT FALSE,
    replayed_at     TIMESTAMP,
    replay_error    TEXT,
    captured_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cdc_events_session    ON cdc_events (session_id, replayed, captured_at);
CREATE INDEX IF NOT EXISTS idx_cdc_events_table      ON cdc_events (session_id, table_name);
CREATE INDEX IF NOT EXISTS idx_cdc_events_unreplayed ON cdc_events (session_id) WHERE replayed = FALSE;


-- ── 4. Cutover Log ────────────────────────────────────────────────────────────
-- Tracks the cutover process step by step.

CREATE TABLE IF NOT EXISTS cutover_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES cdc_sessions(id) ON DELETE CASCADE,
    step            VARCHAR(100) NOT NULL,
    status          VARCHAR(50) DEFAULT 'pending',
    details         TEXT,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cutover_log_session ON cutover_log (session_id);


-- ── 5. Policy Rules ───────────────────────────────────────────────────────────
-- Organizational policies enforced before migration executes.

CREATE TABLE IF NOT EXISTS policy_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    name            VARCHAR(255) NOT NULL,
    policy_type     VARCHAR(100) NOT NULL,
    -- forbidden_lossy_conversion | require_approval | max_downtime_minutes
    -- require_validation | forbidden_table_drop | require_backup_before_cutover
    config          JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_rules_tenant ON policy_rules (tenant_id, policy_type);
