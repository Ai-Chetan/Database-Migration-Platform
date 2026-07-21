-- ============================================================================
-- Migration Platform Kernel — Seed Roles Table
-- File: migration/db_migrations/017_seed_roles.sql
--
-- The live database inspection confirmed the 'roles' table exists (created
-- by 004_security_saas.sql) but has 0 rows. enterprise/security/rbac/auth.py
-- has a hardcoded fallback permission list when a role row is missing, so
-- the platform technically still works without this — but a properly
-- seeded table is the correct, intended source of truth and lets you
-- edit permissions via SQL/admin UI later without redeploying code.
--
-- Permission format matches what enterprise/security/rbac/auth.py and the
-- frontend's utils/permissions.ts both already use: "resource:action"
-- (e.g. "jobs:read", "jobs:*", "connections:write").
--
-- Run:
--   psql -U postgres -d migration_metadata -f 017_seed_roles.sql
-- ============================================================================

INSERT INTO roles (name, description, permissions, is_system) VALUES

('platform_admin',
 'Full system access. Can manage all settings, users, and tenants.',
 '["*:*"]'::jsonb,
 TRUE),

('tenant_admin',
 'Manage users and settings within their tenant. Cannot change platform-level configuration.',
 '["users:*", "connections:*", "jobs:*", "jobs:approve", "schema:*", "operations:*",
   "masking:*", "scheduler:*", "reports:*", "knowledge:*", "audit:read",
   "tenant:*"]'::jsonb,
 TRUE),

('migration_admin',
 'Create and manage migrations. Cannot manage users, platform settings, or approve migrations they created.',
 '["connections:*", "jobs:create", "jobs:read", "jobs:write", "jobs:start",
   "jobs:pause", "jobs:resume", "jobs:cancel", "schema:*", "operations:read",
   "operations:write", "masking:*", "scheduler:*", "reports:*", "knowledge:*"]'::jsonb,
 TRUE),

('migration_operator',
 'Run and monitor existing migrations. Cannot create new connections or jobs.',
 '["connections:read", "jobs:read", "jobs:start", "jobs:pause", "jobs:resume",
   "schema:read", "operations:read", "operations:write", "scheduler:read",
   "reports:read", "knowledge:read"]'::jsonb,
 TRUE),

('read_only',
 'View all data across the platform with no modification rights.',
 '["connections:read", "jobs:read", "schema:read", "operations:read",
   "masking:read", "scheduler:read", "reports:read", "knowledge:read"]'::jsonb,
 TRUE),

('auditor',
 'View audit logs and reports only. No access to operational pages.',
 '["audit:read", "reports:read"]'::jsonb,
 TRUE),

('api_client',
 'Programmatic API access only. No UI access.',
 '["api:access"]'::jsonb,
 TRUE)

ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    permissions  = EXCLUDED.permissions;


\echo '--- Verification: roles table now seeded ---'
SELECT name, description, permissions FROM roles ORDER BY name;
