-- ============================================================================
-- Migration Platform Kernel — Fix User Roles Check Constraint + Admin User
-- Management columns
-- File: migration/db_migrations/019_fix_user_roles_and_admin_management.sql
--
-- ROOT CAUSE THIS FIXES:
--   users_role_check was created back when the platform only had 3 roles
--   ('admin', 'user', 'viewer'). The RBAC system was upgraded to 7 roles
--   long ago (see 017_seed_roles.sql, enterprise/security/rbac/auth.py) but
--   this CHECK constraint on the `users` table itself was never migrated.
--   Every invite-accept / direct user-create for any role other than the
--   literal strings 'admin'/'user'/'viewer' has been failing with:
--       psycopg2.errors.CheckViolation: new row for relation "users"
--       violates check constraint "users_role_check"
--   This is why test_e2e_api.py showed 500 errors creating every demo
--   account except the first tenant_admin (whose row happened to be
--   written by a different, older code path before this constraint existed
--   in some environments — inconsistent, which is exactly why constraints
--   like this must never drift from the application's real role list).
--
-- ALSO ADDS (needed for the new admin-direct-create-user flow, replacing
-- the invitation-based flow per product decision):
--   users.phone               - optional contact number, shown in the
--                                admin "Add user" form
--   users.must_change_password- sent by admin-created accounts so the
--                                user is forced to set their own password
--                                on first login instead of keeping the
--                                one the admin typed in. (The column
--                                `force_password_change` already exists
--                                and does exactly this - we just make sure
--                                it defaults sanely and is indexed with
--                                is_active for the login-time check.)
--
-- Run:
--   psql -U postgres -d migration_metadata -f 019_fix_user_roles_and_admin_management.sql
-- ============================================================================

-- ── Fix the CHECK constraint ─────────────────────────────────────────────────
-- Drop the stale constraint and replace it with the real 7-role list.
-- Kept as a CHECK (not a FK to roles.name) on purpose: roles.name is
-- free-text without a uniqueness/FK-friendly setup in this schema, and a
-- CHECK constraint gives us the same safety without a schema-wide refactor.
-- If a new role is added to enterprise/security/rbac/auth.py and
-- 017_seed_roles.sql in the future, it MUST also be added here.

ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_check;

ALTER TABLE public.users ADD CONSTRAINT users_role_check CHECK (
    (role)::text = ANY (ARRAY[
        'platform_admin',
        'tenant_admin',
        'migration_admin',
        'migration_operator',
        'read_only',
        'auditor',
        'api_client'
    ]::text[])
);

-- Existing rows created before this fix (e.g. any that slipped in with the
-- old 'admin'/'user'/'viewer' values under a permissive environment) are
-- mapped forward to their closest equivalent so the ALTER above doesn't
-- fail against real data.
UPDATE public.users SET role = 'tenant_admin'      WHERE role = 'admin';
UPDATE public.users SET role = 'migration_operator' WHERE role = 'user';
UPDATE public.users SET role = 'read_only'          WHERE role = 'viewer';


-- ── Columns needed for admin-direct-create user management ──────────────────

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS phone VARCHAR(30);

-- force_password_change already exists (added upstream) - just make sure it
-- has a real default and isn't NULL for older rows.
ALTER TABLE public.users
    ALTER COLUMN force_password_change SET DEFAULT FALSE;
UPDATE public.users SET force_password_change = FALSE WHERE force_password_change IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_tenant_active ON public.users (tenant_id, is_active);


-- ── password_reset_tokens: index for fast lookup + cleanup ──────────────────
-- Table already exists (see schema dump) with the right columns; it was
-- simply never used because the /auth/forgot-password and
-- /auth/reset-password endpoints didn't exist yet (added alongside this
-- migration). Add the index that endpoint needs to look up a token quickly.

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_hash
    ON public.password_reset_tokens (token_hash);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user
    ON public.password_reset_tokens (user_id, created_at DESC);


-- ── Verification ──────────────────────────────────────────────────────────

\echo '--- Verification: users_role_check now allows all 7 roles ---'
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.users'::regclass AND conname = 'users_role_check';

\echo '--- Verification: no rows violate the new constraint ---'
SELECT role, count(*) FROM public.users GROUP BY role ORDER BY role;
