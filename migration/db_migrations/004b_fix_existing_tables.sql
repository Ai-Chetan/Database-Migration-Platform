-- ============================================================
-- Fix for 004_security_saas.sql errors
-- File: migration/backend/enterprise/db_migrations/004b_fix_existing_tables.sql
--
-- Your tenants, users, api_keys, secrets_vault tables already existed
-- from earlier migrations but were missing the new columns.
-- This script safely adds the missing columns.
--
-- Run this AFTER 004_security_saas.sql:
--   psql -U postgres -d migration_metadata -f 004b_fix_existing_tables.sql
-- ============================================================


-- ── Fix tenants table ─────────────────────────────────────────────────────────
-- Your existing tenants table is missing: slug, status, plan_name, max_users, etc.

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS slug            VARCHAR(100),
    ADD COLUMN IF NOT EXISTS status          VARCHAR(50)  DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS plan_name       VARCHAR(100) DEFAULT 'free',
    ADD COLUMN IF NOT EXISTS max_users       INTEGER      DEFAULT 3,
    ADD COLUMN IF NOT EXISTS max_jobs        INTEGER      DEFAULT 10,
    ADD COLUMN IF NOT EXISTS max_connections INTEGER      DEFAULT 5,
    ADD COLUMN IF NOT EXISTS max_workers     INTEGER      DEFAULT 2,
    ADD COLUMN IF NOT EXISTS storage_gb_limit INTEGER     DEFAULT 10,
    ADD COLUMN IF NOT EXISTS billing_email   VARCHAR(255),
    ADD COLUMN IF NOT EXISTS metadata        JSONB        DEFAULT '{}';

-- Backfill slug from name for existing rows (slug = lowercase name with spaces replaced)
UPDATE tenants SET slug = LOWER(REGEXP_REPLACE(name, '[^a-zA-Z0-9]', '-', 'g'))
WHERE slug IS NULL;

-- Now make slug NOT NULL and UNIQUE after backfilling
ALTER TABLE tenants ALTER COLUMN slug SET NOT NULL;

-- Add unique constraint only if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tenants_slug_key'
    ) THEN
        ALTER TABLE tenants ADD CONSTRAINT tenants_slug_key UNIQUE (slug);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_tenants_slug   ON tenants (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status);


-- ── Fix users table ───────────────────────────────────────────────────────────
-- Add missing columns to existing users table

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_enabled  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS mfa_secret   TEXT,
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP;

-- Make sure role column exists (it should from your original table)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS role VARCHAR(100) DEFAULT 'migration_operator';


-- ── Fix secrets_vault table ───────────────────────────────────────────────────
-- Your existing secrets_vault is missing key_name column

ALTER TABLE secrets_vault
    ADD COLUMN IF NOT EXISTS key_name        VARCHAR(255),
    ADD COLUMN IF NOT EXISTS encrypted_value TEXT,
    ADD COLUMN IF NOT EXISTS description     TEXT,
    ADD COLUMN IF NOT EXISTS created_by_id   UUID REFERENCES users(id);

-- Backfill key_name from existing data if any (use 'unknown' as fallback)
UPDATE secrets_vault SET key_name = 'migrated_secret_' || id::TEXT WHERE key_name IS NULL;

-- Make key_name NOT NULL after backfill
ALTER TABLE secrets_vault ALTER COLUMN key_name SET NOT NULL;

-- Add unique constraint on (tenant_id, key_name) if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'idx_secrets_tenant_key'
    ) THEN
        ALTER TABLE secrets_vault ADD CONSTRAINT idx_secrets_tenant_key
            UNIQUE (tenant_id, key_name);
    END IF;
END $$;


-- ── Fix api_keys table ────────────────────────────────────────────────────────
-- Add any missing columns

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS key_prefix  VARCHAR(20),
    ADD COLUMN IF NOT EXISTS key_hash    TEXT,
    ADD COLUMN IF NOT EXISTS role        VARCHAR(100) DEFAULT 'api_client',
    ADD COLUMN IF NOT EXISTS scopes      JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMP,
    ADD COLUMN IF NOT EXISTS is_active   BOOLEAN DEFAULT TRUE;

-- Backfill key_prefix and key_hash for any existing rows
UPDATE api_keys SET key_prefix = SUBSTRING(id::TEXT, 1, 12) WHERE key_prefix IS NULL;
UPDATE api_keys SET key_hash   = id::TEXT WHERE key_hash IS NULL;


-- ── Seed local development tenant ─────────────────────────────────────────────
-- Only insert if it doesn't already exist

INSERT INTO tenants (id, name, slug, status, plan_name, max_users, max_jobs, max_connections, max_workers)
SELECT
    'a0000000-0000-0000-0000-000000000001'::UUID,
    'Local Development',
    'local',
    'active',
    'enterprise',
    999, 999, 999, 999
WHERE NOT EXISTS (
    SELECT 1 FROM tenants WHERE slug = 'local'
);


-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT 'tenants columns' AS check_name,
       string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns
FROM information_schema.columns
WHERE table_name = 'tenants' AND table_schema = 'public';

SELECT 'secrets_vault columns' AS check_name,
       string_agg(column_name, ', ' ORDER BY ordinal_position) AS columns
FROM information_schema.columns
WHERE table_name = 'secrets_vault' AND table_schema = 'public';
