import { Role } from '@/types'

/**
 * Coarse-grained permission matrix mirroring the backend RBAC service.
 * Used purely for UI gating (hiding buttons/routes); the backend is the
 * source of truth and re-validates every request.
 */
export const ROLE_PERMISSIONS: Record<Role, string[]> = {
  platform_admin: ['*'],
  tenant_admin: [
    'connections:*',
    'jobs:*',
    'schema:*',
    'masking:*',
    'scheduler:*',
    'reports:*',
    'knowledge:*',
    'users:*',
    'audit:read',
    'operations:*',
  ],
  migration_admin: [
    'connections:*',
    'jobs:*',
    'schema:*',
    'masking:*',
    'scheduler:*',
    'reports:*',
    'knowledge:*',
    'operations:*',
  ],
  migration_operator: [
    'connections:read',
    'jobs:read',
    'jobs:start',
    'operations:*',
    'schema:read',
    'reports:read',
  ],
  read_only: ['connections:read', 'jobs:read', 'schema:read', 'reports:read', 'knowledge:read'],
  auditor: ['audit:read', 'jobs:read', 'reports:read', 'users:read'],
  api_client: ['jobs:*', 'connections:*'],
}

export function hasPermission(role: Role | undefined, permission: string): boolean {
  if (!role) return false
  const perms = ROLE_PERMISSIONS[role] || []
  if (perms.includes('*')) return true
  const [resource] = permission.split(':')
  return perms.includes(permission) || perms.includes(`${resource}:*`)
}

export const ROLE_LABELS: Record<Role, string> = {
  platform_admin: 'Platform Admin',
  tenant_admin: 'Tenant Admin',
  migration_admin: 'Migration Admin',
  migration_operator: 'Migration Operator',
  read_only: 'Read Only',
  auditor: 'Auditor',
  api_client: 'API Client',
}
