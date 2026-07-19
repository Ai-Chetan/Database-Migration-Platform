-- ============================================================
-- Migration Platform Kernel - Part 12: Authentication & Users (CORRECTED)
-- File: migration/db_migrations/015_users_auth_fix.sql
--
-- This REPLACES the earlier 015_users_auth.sql, which incorrectly assumed
-- 'users' and 'user_sessions' did not already exist. They do - from an
-- earlier part of this project - and already contain 1 real user
-- (aarya@gmail.com, role 'admin'). This migration ALTERs the existing
-- tables to add only what's missing, and does NOT touch existing rows
-- except for one deliberate, safe role-value migration described below.
--
-- Run:
--   psql -U postgres -d migration_metadata -f 015_users_auth_fix.sql
-- ============================================================


-- 1. Extend the existing users table
-- (tenant_id stays UUID as it already is - we do NOT change its type.
--  full_name stays as the name column - the application code has been
--  updated to read/write 'full_name' instead of introducing a duplicate
--  'name' column.)

ALTER TABLE users ADD COLUMN IF NOT EXISTS force_password_change   BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts   INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until            TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by              UUID;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMP DEFAULT NOW();

UPDATE users SET updated_at = created_at WHERE updated_at IS NULL;

-- Migrate the existing role value 'admin' -> 'platform_admin' so it matches
-- the 7-role model this platform now uses. This affects ONLY rows currently
-- holding the old generic 'admin' value - a one-time, one-directional fix.
UPDATE users SET role = 'platform_admin' WHERE role = 'admin';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_users_email_lower_unique'
    ) THEN
        CREATE UNIQUE INDEX idx_users_email_lower_unique ON users (LOWER(email));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_tenant_active ON users (tenant_id, is_active);


-- 2. Extend the existing user_sessions table
-- (token_hash already exists and is unused by the new code - left in place,
--  harmless. token_jti is the new column the JWT-based session tracking uses.)

ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS token_jti       VARCHAR(255);
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS issued_at       TIMESTAMP DEFAULT NOW();
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_at      TIMESTAMP;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_reason  VARCHAR(100);

UPDATE user_sessions SET issued_at = created_at WHERE issued_at IS NULL;

UPDATE user_sessions SET revoked_at = NOW(), revoked_reason = 'legacy_migration'
WHERE is_revoked = TRUE AND revoked_at IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_sessions_jti'
    ) THEN
        CREATE UNIQUE INDEX idx_sessions_jti ON user_sessions (token_jti)
        WHERE token_jti IS NOT NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions (user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON user_sessions (expires_at)
    WHERE revoked_at IS NULL;


-- 3. Password Reset Tokens (new table - no conflict, safe as-is)

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(255) NOT NULL,
    expires_at      TIMESTAMP NOT NULL,
    used_at         TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reset_tokens_user ON password_reset_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_expiry ON password_reset_tokens (expires_at)
    WHERE used_at IS NULL;


-- 4. Role Definitions (new table - no conflict, safe as-is)

CREATE TABLE IF NOT EXISTS role_definitions (
    role_name       VARCHAR(50) PRIMARY KEY,
    display_name    VARCHAR(100) NOT NULL,
    description     TEXT,
    permissions     TEXT[] NOT NULL DEFAULT '{}',
    rank            INTEGER NOT NULL
);

INSERT INTO role_definitions (role_name, display_name, description, permissions, rank) VALUES
('platform_admin', 'Platform Admin',
 'Full system access. Can manage all settings, users, and tenants.',
 ARRAY['*'], 1),

('tenant_admin', 'Tenant Admin',
 'Manage users and settings within their tenant. Cannot change platform-level configuration.',
 ARRAY['manage:users','manage:tenant_settings','configure:policies','configure:notifiers',
       'maintenance:mode','emergency:stop','view:audit','create:connection','create:job',
       'start:job','pause:job','resume:job','cancel:job','kill:worker','create:masking',
       'create:schedule','view:knowledge'], 2),

('migration_admin', 'Migration Admin',
 'Create and manage migrations. Cannot manage users or platform settings.',
 ARRAY['create:connection','create:job','start:job','pause:job','resume:job','cancel:job',
       'kill:worker','create:masking','create:schedule','view:knowledge','view:audit_own'], 3),

('migration_operator', 'Migration Operator',
 'Run and monitor existing migrations. Cannot create new connections or jobs.',
 ARRAY['start:job','pause:job','resume:job','view:knowledge'], 4),

('read_only', 'Read Only',
 'View all data across the platform with no modification rights.',
 ARRAY['view:jobs','view:connections','view:schema','view:reports','view:knowledge'], 5),

('auditor', 'Auditor',
 'View audit logs and reports only. No access to operational pages.',
 ARRAY['view:audit','view:reports'], 6),

('api_client', 'API Client',
 'Programmatic API access only. No UI access.',
 ARRAY['api:access'], 7)
ON CONFLICT (role_name) DO NOTHING;


-- 5. Verification output

\echo '--- Verification: existing user after migration ---'
SELECT id, tenant_id, email, full_name, role, is_active,
       force_password_change, failed_login_attempts, locked_until, updated_at
FROM users;

\echo '--- Verification: role_definitions seeded ---'
SELECT role_name, display_name, rank FROM role_definitions ORDER BY rank;
