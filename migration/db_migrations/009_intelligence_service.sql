-- ============================================================
-- Migration Platform Kernel — Part 4: Intelligence Service
-- File: migration/backend/intelligence_service/db_migrations/009_intelligence_service.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 009_intelligence_service.sql
-- ============================================================


-- ── 1. Assessment Reports ─────────────────────────────────────────────────────
-- Stores generated assessment reports. Every call to POST /assess produces
-- one row. Reports are read-only (the service never executes anything).

CREATE TABLE IF NOT EXISTS assessment_reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(100) NOT NULL DEFAULT 'local',
    connection_id       UUID,
    schema_version_id   UUID,
    complexity          VARCHAR(20)  NOT NULL,   -- LOW | MEDIUM | HIGH | CRITICAL
    risk_level          VARCHAR(20)  NOT NULL,   -- low | medium | high | critical
    total_tables        INTEGER DEFAULT 0,
    total_rows          BIGINT  DEFAULT 0,
    total_size_gb       NUMERIC(12,3) DEFAULT 0,
    estimated_duration  VARCHAR(100),
    recommended_workers INTEGER DEFAULT 4,
    recommended_chunk_strategy VARCHAR(100),
    blocking_issues     JSONB DEFAULT '[]',
    warnings            JSONB DEFAULT '[]',
    recommendations     JSONB DEFAULT '[]',
    table_breakdown     JSONB DEFAULT '[]',
    full_report         JSONB DEFAULT '{}',
    generated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessment_reports_tenant
    ON assessment_reports (tenant_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_assessment_reports_connection
    ON assessment_reports (connection_id);


-- ── 2. Data Quality Scan Results ─────────────────────────────────────────────
-- Stores per-table data quality findings. Separate from schema_validation_results
-- (which is post-migration); these are pre-migration data quality issues.

CREATE TABLE IF NOT EXISTS data_quality_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    connection_id   UUID,
    table_name      VARCHAR(255) NOT NULL,
    check_type      VARCHAR(100) NOT NULL,
    -- duplicate_pk | null_pk | broken_fk | orphan_rows | invalid_date
    -- | encoding_issue | oversized_value | circular_ref | null_required
    severity        VARCHAR(20)  NOT NULL,  -- error | warning | info
    affected_count  BIGINT DEFAULT 0,
    affected_pct    NUMERIC(6,3) DEFAULT 0,
    sample_values   JSONB DEFAULT '[]',
    details         TEXT,
    recommendation  TEXT,
    scanned_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dq_results_connection
    ON data_quality_results (connection_id, table_name);
CREATE INDEX IF NOT EXISTS idx_dq_results_severity
    ON data_quality_results (severity, check_type);
