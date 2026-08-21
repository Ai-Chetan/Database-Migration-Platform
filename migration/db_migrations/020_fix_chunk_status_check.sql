-- ============================================================================
-- Migration Platform Kernel — Fix migration_chunks status check constraint
-- File: migration/db_migrations/020_fix_chunk_status_check.sql
--
-- ROOT CAUSE THIS FIXES:
--   migration_chunks_status_check only allowed
--   ('pending','running','completed','failed'), but
--   operations/chunk_control/chunk_control.py's skip_chunk() sets
--   status='skipped' - a real, reachable code path from the Operations
--   Console's "Skip chunk" button. Every call would fail with:
--       psycopg2.errors.CheckViolation: new row for relation
--       "migration_chunks" violates check constraint
--       "migration_chunks_status_check"
--   Same class of bug as 019's users_role_check fix - a CHECK constraint
--   that drifted out of sync with the application code that writes to it.
--
-- Run:
--   psql -U postgres -d migration_metadata -f 020_fix_chunk_status_check.sql
-- ============================================================================

ALTER TABLE public.migration_chunks DROP CONSTRAINT IF EXISTS migration_chunks_status_check;

ALTER TABLE public.migration_chunks ADD CONSTRAINT migration_chunks_status_check CHECK (
    (status)::text = ANY (ARRAY[
        'pending',
        'running',
        'completed',
        'failed',
        'skipped'
    ]::text[])
);

\echo '--- Verification ---'
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.migration_chunks'::regclass AND conname = 'migration_chunks_status_check';
