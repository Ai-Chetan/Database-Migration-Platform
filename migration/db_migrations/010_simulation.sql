-- ============================================================
-- Migration Platform Kernel — Part 5: Simulation Engine
-- File: migration/backend/simulation/db_migrations/010_simulation.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 010_simulation.sql
-- ============================================================


-- ── 1. Simulation Runs ────────────────────────────────────────────────────────
-- Each call to POST /simulate creates one row. Stores inputs and results
-- so users can compare scenarios side-by-side.

CREATE TABLE IF NOT EXISTS simulation_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(100) NOT NULL DEFAULT 'local',
    connection_id       UUID,
    name                VARCHAR(255),             -- user-supplied label, e.g. "4 workers baseline"
    -- Input parameters
    worker_count        INTEGER NOT NULL,
    chunk_size_strategy VARCHAR(100) DEFAULT 'size_based',
    chunk_size_override INTEGER,                  -- NULL = use adaptive planner result
    source_engine       VARCHAR(50) DEFAULT 'mysql',
    target_engine       VARCHAR(50) DEFAULT 'mysql',
    -- Computed results
    estimated_duration_sec  BIGINT,
    estimated_duration_str  VARCHAR(100),
    estimated_rows_per_sec  BIGINT,
    estimated_mb_per_sec    NUMERIC(10,2),
    estimated_cpu_pct       NUMERIC(5,2),
    estimated_network_gb    NUMERIC(10,3),
    estimated_target_storage_gb NUMERIC(12,3),
    failure_probability_pct NUMERIC(5,2),
    bottleneck              VARCHAR(100),    -- "source_io" | "network" | "target_write" | "worker_cpu"
    table_breakdown         JSONB DEFAULT '[]',
    recommendations         JSONB DEFAULT '[]',
    -- Metadata
    data_source             VARCHAR(50) DEFAULT 'metadata_catalog',
    -- metadata_catalog | manual_input | benchmark_history
    created_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_simulation_runs_tenant
    ON simulation_runs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_connection
    ON simulation_runs (connection_id);
