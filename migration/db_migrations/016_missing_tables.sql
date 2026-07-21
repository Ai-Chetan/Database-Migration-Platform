-- ============================================================================
-- Migration Platform Kernel — Missing Tables Completion
-- File: migration/db_migrations/016_missing_tables.sql
--
-- Creates the 5 tables confirmed genuinely absent by the live database
-- inspection (000_inspect_database.sql report): benchmark_records,
-- maintenance_mode, operations_actions, schema_drift_events,
-- self_tuning_actions.
--
-- Column choices below match the REAL, existing schema conventions found
-- in your live database (e.g., worker identity via worker_name matching
-- worker_heartbeats.worker_name, tenant_id as UUID matching
-- migration_approvals.tenant_id, job_id FK to the real migration_jobs.id).
--
-- Run:
--   psql -U postgres -d migration_metadata -f 016_missing_tables.sql
-- ============================================================================


-- ── schema_drift_events ───────────────────────────────────────────────────
-- Records DDL changes detected on source during an active migration.

CREATE TABLE IF NOT EXISTS schema_drift_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    table_name      VARCHAR(255) NOT NULL,
    drift_type      VARCHAR(100) NOT NULL,
    -- column_added | column_dropped | column_type_changed | column_renamed
    -- | index_added | index_dropped | table_dropped | table_added
    column_name     VARCHAR(255),
    old_definition  JSONB,
    new_definition  JSONB,
    severity        VARCHAR(20) DEFAULT 'warning',   -- warning | critical
    detected_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    action_taken    VARCHAR(100) DEFAULT 'paused',
    resolved_at     TIMESTAMP,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_drift_events_job ON schema_drift_events (job_id, detected_at DESC);


-- ── self_tuning_actions ───────────────────────────────────────────────────
-- Records every automatic worker/chunk-size adjustment made during a
-- migration by the Self-Tuning Engine.

CREATE TABLE IF NOT EXISTS self_tuning_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    action_type     VARCHAR(100) NOT NULL,
    -- increase_workers | decrease_workers | increase_chunk_size | decrease_chunk_size
    -- | throttle_source | throttle_target | pause_for_load
    before_value    JSONB NOT NULL DEFAULT '{}',
    after_value     JSONB NOT NULL DEFAULT '{}',
    reason          TEXT,
    triggered_by    VARCHAR(100),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tuning_actions_job ON self_tuning_actions (job_id, created_at DESC);


-- ── benchmark_records ─────────────────────────────────────────────────────
-- Historical performance data per completed migration, used to calibrate
-- the Simulation Engine with real observed throughput instead of
-- hardcoded estimates.

CREATE TABLE IF NOT EXISTS benchmark_records (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                UUID REFERENCES migration_jobs(id) ON DELETE SET NULL,
    tenant_id             UUID REFERENCES tenants(id),
    source_engine         VARCHAR(50),
    target_engine         VARCHAR(50),
    worker_count          INTEGER,
    chunk_strategy        VARCHAR(100),
    total_rows            BIGINT,
    total_size_gb         NUMERIC(12,3),
    duration_sec          INTEGER,
    avg_rows_per_sec      BIGINT,
    avg_mb_per_sec        NUMERIC(10,2),
    peak_rows_per_sec     BIGINT,
    min_rows_per_sec      BIGINT,
    avg_chunk_duration_ms INTEGER,
    failed_chunks         INTEGER DEFAULT 0,
    retried_chunks        INTEGER DEFAULT 0,
    table_benchmarks      JSONB DEFAULT '[]',
    environment_info      JSONB DEFAULT '{}',
    recorded_at           TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_benchmark_engines ON benchmark_records (source_engine, target_engine, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_tenant  ON benchmark_records (tenant_id, recorded_at DESC);


-- ── operations_actions ────────────────────────────────────────────────────
-- Immutable audit trail of every manual operator action taken via the
-- Operations Console (pause/resume/kill worker, retry/skip chunk,
-- pause/resume/cancel job, maintenance mode, emergency stop).

CREATE TABLE IF NOT EXISTS operations_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id),
    operator_id     UUID REFERENCES users(id),
    action_type     VARCHAR(100) NOT NULL,
    -- pause_worker | resume_worker | kill_worker | quarantine_worker
    -- | reassign_chunk | retry_chunk | skip_chunk
    -- | scale_workers | throttle_job | force_cutover
    -- | pause_job | resume_job | cancel_job | restart_job
    -- | maintenance_mode_on | maintenance_mode_off | emergency_stop
    -- | drain_workers | rerun_validation
    resource_type   VARCHAR(50),         -- worker | chunk | job | system
    resource_id     VARCHAR(255),        -- worker_name, chunk id, or job id (text to allow any shape)
    before_state    JSONB DEFAULT '{}',
    after_state     JSONB DEFAULT '{}',
    reason          TEXT,
    status          VARCHAR(50) DEFAULT 'completed',
    error_message   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ops_actions_resource ON operations_actions (resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ops_actions_tenant    ON operations_actions (tenant_id, created_at DESC);


-- ── maintenance_mode ───────────────────────────────────────────────────────
-- Platform-wide (or per-tenant) maintenance mode state.

CREATE TABLE IF NOT EXISTS maintenance_mode (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id),
    is_active       BOOLEAN DEFAULT FALSE,
    reason          TEXT,
    activated_by    UUID REFERENCES users(id),
    activated_at    TIMESTAMP,
    deactivated_at  TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- One global row for platform-wide maintenance (tenant_id NULL = platform-wide).
-- Per-tenant rows can be added later if per-tenant maintenance mode is needed.
INSERT INTO maintenance_mode (tenant_id, is_active)
SELECT NULL, FALSE
WHERE NOT EXISTS (SELECT 1 FROM maintenance_mode WHERE tenant_id IS NULL);

CREATE UNIQUE INDEX IF NOT EXISTS idx_maintenance_mode_global
    ON maintenance_mode ((tenant_id IS NULL)) WHERE tenant_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_maintenance_mode_tenant ON maintenance_mode (tenant_id);


-- ── Verification ───────────────────────────────────────────────────────────

\echo '--- Verification: all 5 tables now exist ---'
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN (
    'schema_drift_events', 'self_tuning_actions', 'benchmark_records',
    'operations_actions', 'maintenance_mode'
)
ORDER BY table_name;
