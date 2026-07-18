-- ============================================================
-- Migration Platform Kernel — Part 12: Authentication & Users
-- File: migration/db_migrations/015_users_auth.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 015_users_auth.sql
--
-- This is the CRITICAL missing piece — without this table,
-- no login, no RBAC, no user management works.
-- ============================================================

-- Requires pgcrypto for gen_random_uuid() — already enabled by earlier migrations
-- CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ── 1. Users ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               VARCHAR(100) NOT NULL DEFAULT 'local',
    email                   VARCHAR(255) NOT NULL,
    name                    VARCHAR(255) NOT NULL,
    password_hash           VARCHAR(255) NOT NULL,
    role                    VARCHAR(50)  NOT NULL DEFAULT 'migration_operator',
    -- platform_admin | tenant_admin | migration_admin | migration_operator
    -- | read_only | auditor | api_client
    is_active               BOOLEAN DEFAULT TRUE,
    force_password_change   BOOLEAN DEFAULT FALSE,
    last_login              TIMESTAMP,
    failed_login_attempts   INTEGER DEFAULT 0,
    locked_until             TIMESTAMP,
    created_by              UUID REFERENCES users(id),
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_tenant
    ON users (tenant_id, LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_tenant_active
    ON users (tenant_id, is_active);


-- ── 2. User Sessions (for logout / token revocation) ────────────────────────────

CREATE TABLE IF NOT EXISTS user_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_jti       VARCHAR(255) NOT NULL,     -- JWT ID claim, unique per token
    ip_address      VARCHAR(64),
    user_agent      TEXT,
    issued_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMP NOT NULL,
    revoked_at      TIMESTAMP,
    revoked_reason  VARCHAR(100)               -- logout | admin_revoked | password_changed
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_jti ON user_sessions (token_jti);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions (user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON user_sessions (expires_at)
    WHERE revoked_at IS NULL;


-- ── 3. Password Reset Tokens ──────────────────────────────────────────────────

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


-- ── 4. Role Definitions (reference table — not enforced by FK, used for /roles) ─

CREATE TABLE IF NOT EXISTS role_definitions (
    role_name       VARCHAR(50) PRIMARY KEY,
    display_name    VARCHAR(100) NOT NULL,
    description     TEXT,
    permissions     TEXT[] NOT NULL DEFAULT '{}',
    rank            INTEGER NOT NULL   -- lower number = more privileged; used to prevent privilege escalation
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


-- ── 5. Seed default platform admin user ───────────────────────────────────────
-- Default credentials: admin@local / ChangeMe123!
-- Password hash below is bcrypt for "ChangeMe123!" — MUST be changed on first login.
-- force_password_change = TRUE ensures this.

INSERT INTO users (tenant_id, email, name, password_hash, role, is_active, force_password_change)
VALUES (
    'local',
    'admin@local',
    'Platform Administrator',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKmKmvz6xC.n7Nu',  -- bcrypt("ChangeMe123!")
    'platform_admin',
    TRUE,
    TRUE
)
ON CONFLICT DO NOTHING;
