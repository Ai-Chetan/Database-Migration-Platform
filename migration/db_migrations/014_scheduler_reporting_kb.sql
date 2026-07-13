-- ============================================================
-- Migration Platform Kernel — Part 11: Scheduler, Reporting, Knowledge Base
-- File: migration/backend/scheduler/db_migrations/014_scheduler_reporting_kb.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 014_scheduler_reporting_kb.sql
-- ============================================================


-- ── 1. Scheduled Jobs ─────────────────────────────────────────────────────────
-- Cron-style scheduling for migrations and intelligence scans.

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    job_type        VARCHAR(100) NOT NULL,
    -- migration | intelligence_scan | data_quality_scan | benchmark | report
    cron_expression VARCHAR(100),          -- "0 2 * * SAT" = Saturday 2 AM
    timezone        VARCHAR(100) DEFAULT 'UTC',
    job_config      JSONB NOT NULL DEFAULT '{}',
    -- For migration: {"source_connection_id": "...", "target_connection_id": "...",
    --                  "mapping_project_id": "...", "worker_count": 8}
    -- For scan:     {"connection_id": "..."}
    require_approval BOOLEAN DEFAULT FALSE,
    is_active       BOOLEAN DEFAULT TRUE,
    last_run_at     TIMESTAMP,
    next_run_at     TIMESTAMP,
    last_status     VARCHAR(50),
    run_count       INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_tenant
    ON scheduled_jobs (tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
    ON scheduled_jobs (next_run_at) WHERE is_active = TRUE;


-- ── 2. Schedule Runs ──────────────────────────────────────────────────────────
-- History of every scheduled job execution.

CREATE TABLE IF NOT EXISTS schedule_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scheduled_job_id UUID NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    triggered_by    VARCHAR(100) DEFAULT 'scheduler',  -- scheduler | manual | api
    status          VARCHAR(50)  DEFAULT 'running',
    -- running | completed | failed | skipped | approval_pending
    migration_job_id UUID,                             -- references migration_jobs(id)
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMP,
    error_message   TEXT,
    result_summary  JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_schedule_runs_job
    ON schedule_runs (scheduled_job_id, started_at DESC);


-- ── 3. Migration Reports ──────────────────────────────────────────────────────
-- Generated reports for completed migrations.

CREATE TABLE IF NOT EXISTS migration_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    job_id          UUID REFERENCES migration_jobs(id),
    report_type     VARCHAR(100) NOT NULL,
    -- migration_summary | validation_report | performance_report
    -- | audit_report | data_quality_report | compliance_report
    format          VARCHAR(20)  DEFAULT 'json',  -- json | pdf | html | csv
    title           VARCHAR(255) NOT NULL,
    content         JSONB DEFAULT '{}',           -- report data
    file_path       TEXT,                         -- if exported to file
    generated_by    VARCHAR(255),
    generated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_migration_reports_job
    ON migration_reports (job_id, report_type);
CREATE INDEX IF NOT EXISTS idx_migration_reports_tenant
    ON migration_reports (tenant_id, generated_at DESC);


-- ── 4. Knowledge Base Entries ─────────────────────────────────────────────────
-- Every completed migration stores knowledge for future reference.

CREATE TABLE IF NOT EXISTS knowledge_base (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    job_id          UUID REFERENCES migration_jobs(id),
    source_engine   VARCHAR(50),
    target_engine   VARCHAR(50),
    entry_type      VARCHAR(100) NOT NULL,
    -- migration_outcome | type_mapping_pattern | performance_pattern
    -- | error_pattern | schema_pattern | cdc_pattern
    title           VARCHAR(500) NOT NULL,
    content         JSONB NOT NULL DEFAULT '{}',
    tags            TEXT[] DEFAULT '{}',
    usefulness_score FLOAT DEFAULT 0.0,     -- 0.0-1.0, updated as entries are referenced
    reference_count INTEGER DEFAULT 0,      -- how many times this entry was used
    is_public       BOOLEAN DEFAULT FALSE,  -- available to all tenants
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kb_engine_pair
    ON knowledge_base (source_engine, target_engine, entry_type);
CREATE INDEX IF NOT EXISTS idx_kb_tenant
    ON knowledge_base (tenant_id, entry_type, usefulness_score DESC);
CREATE INDEX IF NOT EXISTS idx_kb_tags
    ON knowledge_base USING GIN (tags);
