-- ============================================================
-- Migration Platform Kernel — Part 3: Metadata Intelligence Layer
-- File: migration/backend/intelligence/db_migrations/008_metadata_intelligence.sql
--
-- Part 3 writes all its data into the metadata_catalog table created
-- in Part 1 (006_kernel_foundation.sql). No new tables needed —
-- the catalog_type discriminator pattern absorbs all new data shapes.
--
-- This file adds:
--   1. A scan_jobs table to track long-running intelligence scans
--   2. Index on metadata_catalog for faster per-connection queries
--   3. Seed the catalog_type vocabulary (documentation only — no constraint)
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 008_metadata_intelligence.sql
-- ============================================================


-- ── 1. Intelligence Scan Jobs ─────────────────────────────────────────────────
-- Tracks a full schema intelligence scan (can take minutes on large DBs).
-- One scan populates metadata_catalog with statistics, relationships,
-- distributions, lob_detection, compression, hot_cold_classification,
-- and growth_rate entries for every table in a schema.

CREATE TABLE IF NOT EXISTS intelligence_scan_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    connection_id   UUID,                         -- references connection_registry(id)
    schema_version_id UUID,                       -- references schema_versions(id)
    status          VARCHAR(50)  DEFAULT 'pending',
    -- pending | running | completed | failed | partial
    tables_total    INTEGER DEFAULT 0,
    tables_scanned  INTEGER DEFAULT 0,
    tables_failed   INTEGER DEFAULT 0,
    catalog_types   JSONB DEFAULT '["statistics","relationship","distribution","lob_detection","compression","hot_cold","growth_rate"]',
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    error_summary   TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_jobs_tenant
    ON intelligence_scan_jobs (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_scan_jobs_connection
    ON intelligence_scan_jobs (connection_id);


-- ── 2. Additional indexes on metadata_catalog for intelligence queries ─────────
-- Part 1 created basic indexes. These support the hot-table and
-- growth-rate queries Intelligence Layer runs most frequently.

CREATE INDEX IF NOT EXISTS idx_metadata_catalog_type_table
    ON metadata_catalog (catalog_type, table_name);

CREATE INDEX IF NOT EXISTS idx_metadata_catalog_connection_type
    ON metadata_catalog (connection_id, catalog_type, computed_at DESC);


-- ── 3. Extend intelligence_scan_jobs with per-table detail ────────────────────
-- Stores per-table scan results as a JSONB array so callers can see
-- exactly which tables succeeded/failed without querying metadata_catalog.

ALTER TABLE intelligence_scan_jobs
    ADD COLUMN IF NOT EXISTS table_results JSONB DEFAULT '[]';
-- [{"table_name": "orders", "status": "completed", "catalog_types": ["statistics","lob_detection"]},
--  {"table_name": "audit_log", "status": "failed", "error": "permission denied"}]
