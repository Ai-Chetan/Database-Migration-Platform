-- ============================================================
-- Phase 10 Part 2 — Enterprise Security + SaaS Foundation
-- File: migration/backend/enterprise/db_migrations/004_security_saas.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 004_security_saas.sql
-- ============================================================


-- ── 1. Tenants (enhanced from basic version) ──────────────────────────────────
-- Full tenant management with plan, usage limits, and billing hook

CREATE TABLE IF NOT EXISTS tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) UNIQUE NOT NULL,     -- URL-safe identifier
    status          VARCHAR(50)  DEFAULT 'active',    -- active | suspended | cancelled
    plan_name       VARCHAR(100) DEFAULT 'free',      -- free | starter | pro | enterprise
    max_users       INTEGER DEFAULT 3,
    max_jobs        INTEGER DEFAULT 10,
    max_connections INTEGER DEFAULT 5,
    max_workers     INTEGER DEFAULT 2,
    storage_gb_limit INTEGER DEFAULT 10,
    billing_email   VARCHAR(255),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status);


-- ── 2. Users ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       VARCHAR(255),
    role            VARCHAR(100) NOT NULL DEFAULT 'migration_operator',
    status          VARCHAR(50)  DEFAULT 'active',    -- active | inactive | invited
    last_login_at   TIMESTAMP,
    mfa_enabled     BOOLEAN DEFAULT FALSE,
    mfa_secret      TEXT,                             -- encrypted TOTP secret
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_tenant   ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_role     ON users (tenant_id, role);


-- ── 3. Roles & Permissions ────────────────────────────────────────────────────
-- RBAC: 7 roles with granular permission sets

CREATE TABLE IF NOT EXISTS roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',   -- list of permission strings
    is_system   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Seed system roles
INSERT INTO roles (name, description, permissions, is_system) VALUES
('platform_admin',
 'Full platform access across all tenants',
 '["*"]'::jsonb, TRUE),

('tenant_admin',
 'Full access within their tenant',
 '["jobs:*","connections:*","users:*","mappings:*","settings:*"]'::jsonb, TRUE),

('migration_admin',
 'Create/manage migrations, cannot manage users',
 '["jobs:*","connections:read","connections:create","mappings:*","schemas:*"]'::jsonb, TRUE),

('migration_operator',
 'Execute and monitor migrations, no create/delete',
 '["jobs:read","jobs:start","jobs:pause","jobs:monitor","connections:read","mappings:read"]'::jsonb, TRUE),

('read_only',
 'View-only access to all resources',
 '["jobs:read","connections:read","mappings:read","schemas:read","workers:read"]'::jsonb, TRUE),

('auditor',
 'Read access + full audit log access',
 '["jobs:read","connections:read","mappings:read","audit:*","reports:*"]'::jsonb, TRUE),

('api_client',
 'Machine-to-machine API access',
 '["jobs:read","jobs:create","jobs:start","connections:read","mappings:read"]'::jsonb, TRUE)

ON CONFLICT (name) DO NOTHING;


-- ── 4. JWT Sessions / API Keys ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,     -- SHA-256 of JWT jti claim
    expires_at      TIMESTAMP NOT NULL,
    ip_address      VARCHAR(50),
    user_agent      TEXT,
    is_revoked      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token   ON user_sessions (token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON user_sessions (expires_at);


CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    name            VARCHAR(255) NOT NULL,
    key_prefix      VARCHAR(20) NOT NULL,      -- first 8 chars shown to user
    key_hash        TEXT NOT NULL UNIQUE,      -- bcrypt hash of full key
    role            VARCHAR(100) DEFAULT 'api_client',
    scopes          JSONB DEFAULT '[]',
    last_used_at    TIMESTAMP,
    expires_at      TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys (tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON api_keys (key_hash);


-- ── 5. Audit Log (immutable) ─────────────────────────────────────────────────
-- Append-only. Never UPDATE or DELETE rows in this table.

CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES tenants(id),
    user_id     UUID REFERENCES users(id),
    action      VARCHAR(255) NOT NULL,          -- e.g. "job.create", "mapping.delete"
    resource_type VARCHAR(100),                 -- "job" | "connection" | "mapping" | ...
    resource_id UUID,
    old_value   JSONB,                          -- state before change
    new_value   JSONB,                          -- state after change
    ip_address  VARCHAR(50),
    user_agent  TEXT,
    status      VARCHAR(50) DEFAULT 'success',  -- success | failed | denied
    error_msg   TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant  ON audit_logs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_logs (action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs (resource_type, resource_id);


-- ── 6. User Invitations ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    invited_by_id   UUID REFERENCES users(id),
    email           VARCHAR(255) NOT NULL,
    role            VARCHAR(100) NOT NULL DEFAULT 'migration_operator',
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMP NOT NULL,
    accepted_at     TIMESTAMP,
    status          VARCHAR(50) DEFAULT 'pending',  -- pending | accepted | expired | cancelled
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invitations_tenant ON user_invitations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_invitations_email  ON user_invitations (email);


-- ── 7. Migration Approval Workflow ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS migration_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES migration_jobs(id) ON DELETE CASCADE,
    tenant_id       UUID REFERENCES tenants(id),
    requested_by_id UUID REFERENCES users(id),
    reviewed_by_id  UUID REFERENCES users(id),
    status          VARCHAR(50) DEFAULT 'pending',  -- pending | approved | rejected | cancelled
    notes           TEXT,
    requested_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    reviewed_at     TIMESTAMP,
    auto_approved   BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_approvals_job    ON migration_approvals (job_id);
CREATE INDEX IF NOT EXISTS idx_approvals_tenant ON migration_approvals (tenant_id, status);


-- ── 8. Migration Templates ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS migration_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    source_db_type  VARCHAR(50),
    target_db_type  VARCHAR(50),
    table_mappings  JSONB DEFAULT '{}',
    chunk_config    JSONB DEFAULT '{}',
    validation_rules JSONB DEFAULT '[]',
    execution_config JSONB DEFAULT '{}',
    tags            JSONB DEFAULT '[]',
    is_public       BOOLEAN DEFAULT FALSE,     -- shared across tenants
    usage_count     INTEGER DEFAULT 0,
    created_by_id   UUID REFERENCES users(id),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_templates_tenant ON migration_templates (tenant_id);
CREATE INDEX IF NOT EXISTS idx_templates_public ON migration_templates (is_public) WHERE is_public = TRUE;


-- ── 9. Tenant Usage Tracking ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    period_month    VARCHAR(7) NOT NULL,        -- "2026-06"
    jobs_created    INTEGER DEFAULT 0,
    jobs_completed  INTEGER DEFAULT 0,
    rows_migrated   BIGINT DEFAULT 0,
    gb_transferred  NUMERIC(10,3) DEFAULT 0,
    worker_hours    NUMERIC(10,2) DEFAULT 0,
    api_calls       INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_tenant_month
    ON tenant_usage (tenant_id, period_month);


-- ── 10. Secrets Vault ─────────────────────────────────────────────────────────
-- For any sensitive value that isn't a DB password
-- (webhook secrets, SMTP passwords, Slack tokens, etc.)

CREATE TABLE IF NOT EXISTS secrets_vault (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_name        VARCHAR(255) NOT NULL,
    encrypted_value TEXT NOT NULL,
    description     TEXT,
    created_by_id   UUID REFERENCES users(id),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_secrets_tenant_key
    ON secrets_vault (tenant_id, key_name);


-- ── 11. Seed default tenant and admin user ────────────────────────────────────
-- Creates the "local" tenant and a default admin for development

INSERT INTO tenants (id, name, slug, status, plan_name, max_users, max_jobs, max_connections, max_workers)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'Local Development',
    'local',
    'active',
    'enterprise',
    999, 999, 999, 999
) ON CONFLICT (slug) DO NOTHING;
