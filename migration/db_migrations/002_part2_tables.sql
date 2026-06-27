-- ============================================================
-- Priority 9 Part 2 — Additional DB Tables
-- File: migration/backend/schema_mapping_service/db_migrations/002_part2_tables.sql
--
-- Run AFTER 001_schema_mapping_tables.sql
-- Run on your local PostgreSQL:
--   psql -U postgres -d migration_metadata -f 002_part2_tables.sql
-- ============================================================


-- ── 1. Constraint Mapping Plans ──────────────────────────────────────────────
-- Stores generated DDL plans per table mapping so they can be
-- retrieved and executed without regenerating every time.

CREATE TABLE IF NOT EXISTS constraint_mapping_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    source_table        VARCHAR(255) NOT NULL,
    target_table        VARCHAR(255) NOT NULL,
    create_ddl          TEXT,           -- Phase 1: CREATE TABLE
    index_ddl           JSONB,          -- Phase 3: CREATE INDEX list
    fk_ddl              JSONB,          -- Phase 4: ADD FK list
    unique_ddl          JSONB,          -- Phase 3: UNIQUE constraints
    conflicts           JSONB,          -- Any detected conflicts
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_constraint_plans_project
    ON constraint_mapping_plans (project_id);


-- ── 2. Migration Execution Log ────────────────────────────────────────────────
-- Tracks each phase of execution at the project level.
-- Workers update migration_chunks; this tracks the schema-level phases.

CREATE TABLE IF NOT EXISTS migration_execution_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    phase           INTEGER NOT NULL,          -- 1=create_tables, 2=data_load, 3=indexes, 4=fks
    phase_name      VARCHAR(100),
    status          VARCHAR(50) DEFAULT 'pending',  -- pending|running|completed|failed
    ddl_executed    TEXT,                      -- The SQL that was run
    error_message   TEXT,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exec_log_project
    ON migration_execution_log (project_id);


-- ── 3. Extend schema_validation_results with extra columns ───────────────────
-- Add sample_rows column to store the actual mismatched rows for debugging.

ALTER TABLE schema_validation_results
    ADD COLUMN IF NOT EXISTS sample_rows JSONB;

ALTER TABLE schema_validation_results
    ADD COLUMN IF NOT EXISTS business_rule_name VARCHAR(255);


-- ── 4. Schema diff cache ──────────────────────────────────────────────────────
-- Cache schema comparison results so /compare doesn't rerun every time.

CREATE TABLE IF NOT EXISTS schema_diff_cache (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_version_id   UUID NOT NULL REFERENCES schema_versions(id) ON DELETE CASCADE,
    target_version_id   UUID NOT NULL REFERENCES schema_versions(id) ON DELETE CASCADE,
    diff_result         JSONB NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_diff_cache_pair
    ON schema_diff_cache (source_version_id, target_version_id);


-- ── 5. Add missing columns to existing tables from Part 1 ────────────────────

-- schema_table_mappings: add source_db and target_db for cross-engine awareness
ALTER TABLE schema_table_mappings
    ADD COLUMN IF NOT EXISTS source_db VARCHAR(50) DEFAULT 'mysql';

ALTER TABLE schema_table_mappings
    ADD COLUMN IF NOT EXISTS target_db VARCHAR(50) DEFAULT 'mysql';

-- mapping_projects: add execution tracking columns
ALTER TABLE mapping_projects
    ADD COLUMN IF NOT EXISTS current_phase   INTEGER DEFAULT 0;

ALTER TABLE mapping_projects
    ADD COLUMN IF NOT EXISTS execution_started_at  TIMESTAMP;

ALTER TABLE mapping_projects
    ADD COLUMN IF NOT EXISTS execution_completed_at TIMESTAMP;
