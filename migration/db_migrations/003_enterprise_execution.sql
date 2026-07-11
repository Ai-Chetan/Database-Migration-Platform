-- ============================================================
-- Phase 10 — Enterprise Execution Engine
-- File: migration/backend/enterprise/db_migrations/003_enterprise_execution.sql
--
-- Run on your local PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 003_enterprise_execution.sql
-- ============================================================


-- ── 1. Connection Registry ─────────────────────────────────────────────────────
-- Stores all database connections centrally with encrypted credentials.
-- Never stores plaintext passwords — uses AES-256 encryption.

CREATE TABLE IF NOT EXISTS connection_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    name            VARCHAR(255) NOT NULL,          -- "Production MySQL", "Staging PG"
    db_type         VARCHAR(50)  NOT NULL,          -- mysql | postgresql | oracle | sqlserver
    host            VARCHAR(500) NOT NULL,
    port            INTEGER      NOT NULL,
    database_name   VARCHAR(255) NOT NULL,
    username        VARCHAR(255) NOT NULL,
    encrypted_password TEXT       NOT NULL,         -- AES-256 encrypted
    encryption_key_id  VARCHAR(100) DEFAULT 'local', -- key reference for rotation
    ssl_enabled     BOOLEAN DEFAULT FALSE,
    ssl_ca_cert     TEXT,
    ssl_client_cert TEXT,
    ssl_client_key  TEXT,
    connection_pool_size INTEGER DEFAULT 5,
    connect_timeout INTEGER DEFAULT 30,
    query_timeout   INTEGER DEFAULT 300,
    extra_params    JSONB DEFAULT '{}',             -- driver-specific params
    last_tested_at  TIMESTAMP,
    last_test_status VARCHAR(50),                   -- success | failed
    last_test_error TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conn_registry_name
    ON connection_registry (tenant_id, name);
CREATE INDEX IF NOT EXISTS idx_conn_registry_tenant
    ON connection_registry (tenant_id);


-- ── 2. Adaptive Chunk Config ───────────────────────────────────────────────────
-- Stores the computed adaptive chunk plan per table per job.
-- Replaces the old fixed 100k chunk size with a computed optimal size.

CREATE TABLE IF NOT EXISTS adaptive_chunk_configs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    table_name          VARCHAR(255) NOT NULL,
    row_count           BIGINT,
    avg_row_size_bytes  INTEGER,
    pk_min              BIGINT,
    pk_max              BIGINT,
    pk_distribution     VARCHAR(50) DEFAULT 'sequential', -- sequential | sparse | uuid
    computed_chunk_size BIGINT NOT NULL,
    computed_chunk_count INTEGER NOT NULL,
    strategy_used       VARCHAR(100),   -- size_based | count_based | streaming | full_table
    estimated_duration_sec INTEGER,
    memory_estimate_mb  INTEGER,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adaptive_chunk_job
    ON adaptive_chunk_configs (job_id);


-- ── 3. Dependency Graph ────────────────────────────────────────────────────────
-- Stores the FK dependency graph for a job so execution order is preserved.

CREATE TABLE IF NOT EXISTS table_dependency_graph (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    table_name      VARCHAR(255) NOT NULL,
    depends_on      JSONB DEFAULT '[]',  -- list of table names this table depends on
    depth_level     INTEGER DEFAULT 0,   -- 0 = no deps, 1 = depends on level 0, etc.
    execution_order INTEGER,             -- final resolved order
    can_parallel    BOOLEAN DEFAULT TRUE, -- can run in parallel with siblings at same depth
    status          VARCHAR(50) DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dep_graph_job
    ON table_dependency_graph (job_id, execution_order);


-- ── 4. Rollback Plans ─────────────────────────────────────────────────────────
-- Every migration plan auto-generates a rollback plan.
-- Stores reversible steps so migration can be undone cleanly.

CREATE TABLE IF NOT EXISTS rollback_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    status          VARCHAR(50) DEFAULT 'ready',  -- ready | executing | completed | failed
    rollback_steps  JSONB NOT NULL,               -- ordered list of rollback operations
    checkpoint_data JSONB DEFAULT '{}',           -- state snapshots for safe rollback
    tables_affected JSONB DEFAULT '[]',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    executed_at     TIMESTAMP,
    completed_at    TIMESTAMP,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_rollback_job
    ON rollback_plans (job_id);


-- ── 5. Rollback Execution Log ─────────────────────────────────────────────────
-- Tracks each step of a rollback as it executes.

CREATE TABLE IF NOT EXISTS rollback_execution_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rollback_plan_id UUID NOT NULL REFERENCES rollback_plans(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL,
    step_type       VARCHAR(100),   -- drop_table | truncate | restore_constraints | delete_rows
    table_name      VARCHAR(255),
    status          VARCHAR(50) DEFAULT 'pending',
    sql_executed    TEXT,
    rows_affected   BIGINT,
    error_message   TEXT,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);


-- ── 6. Resource Governor State ────────────────────────────────────────────────
-- Tracks real-time resource usage to enable throttling decisions.

CREATE TABLE IF NOT EXISTS resource_governor_state (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    recorded_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    source_db_cpu_pct   NUMERIC(5,2),
    source_db_conn_count INTEGER,
    target_db_cpu_pct   NUMERIC(5,2),
    target_db_conn_count INTEGER,
    worker_count_active  INTEGER,
    redis_queue_depth    INTEGER,
    rows_per_sec         BIGINT,
    throttle_applied     BOOLEAN DEFAULT FALSE,
    throttle_reason      VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_resource_governor_job
    ON resource_governor_state (job_id, recorded_at DESC);


-- ── 7. Extend migration_jobs with enterprise columns ──────────────────────────

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS source_connection_id UUID REFERENCES connection_registry(id);

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS target_connection_id UUID REFERENCES connection_registry(id);

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS dependency_graph_built BOOLEAN DEFAULT FALSE;

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS rollback_plan_id UUID;

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS throttle_active BOOLEAN DEFAULT FALSE;

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS max_workers INTEGER DEFAULT 4;

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255);

ALTER TABLE migration_jobs
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP;

-- ── 8. Extend migration_tables with dependency columns ────────────────────────

ALTER TABLE migration_tables
    ADD COLUMN IF NOT EXISTS execution_order INTEGER DEFAULT 0;

ALTER TABLE migration_tables
    ADD COLUMN IF NOT EXISTS depth_level INTEGER DEFAULT 0;

ALTER TABLE migration_tables
    ADD COLUMN IF NOT EXISTS depends_on JSONB DEFAULT '[]';

ALTER TABLE migration_tables
    ADD COLUMN IF NOT EXISTS computed_chunk_size BIGINT DEFAULT 100000;

ALTER TABLE migration_tables
    ADD COLUMN IF NOT EXISTS avg_row_size_bytes INTEGER;
