-- Migration 018: Add job naming + wire connection references
-- File: migration/db_migrations/018_job_name_and_connections.sql
--
-- migration_jobs already has source_connection_id / target_connection_id /
-- max_workers columns (created in an earlier migration) but the job-creation
-- code never populated them - it only ever wrote source_config/target_config
-- JSONB blobs. This migration adds the one column that was genuinely never
-- created at all: a human-readable job name. Everything else needed for
-- the frontend<->backend integration fix already existed in the schema,
-- just wasn't being used by the application code (fixed separately in
-- control_plane/app/repositories/migration_job_repository.py and
-- control_plane/app/routers/jobs.py).

ALTER TABLE migration_jobs ADD COLUMN IF NOT EXISTS name VARCHAR(255);

COMMENT ON COLUMN migration_jobs.name IS
  'Human-readable job name set at creation time via the New Migration wizard. Nullable for jobs created before this column existed.';
