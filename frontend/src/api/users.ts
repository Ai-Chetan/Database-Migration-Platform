import apiClient from './client'
import { User, Role } from '@/types'

// ── Raw backend response shapes ────────────────────────────────────────────
// Matches enterprise/routers/tenants.py + enterprise/saas/tenants/tenant_service.py
// exactly (verified against backend source, not assumed).
//
// CHANGE: this whole file previously called endpoints that don't exist on
// the backend at all - /users, /users/invite, /users/{id}/role,
// /users/{id}/deactivate. The real backend has always required creating
// users through an invitation (POST /auth/invite), and the invite-based
// flow itself has now been replaced with the admin-direct-create flow at
// POST /tenants/{tenant_id}/users per product decision. Every method here
// is tenant-scoped because that's how the backend routes are structured -
// callers must pass the current user's tenant_id (available from
// useAuthStore().user.tenant_id).

interface RawUser {
  id: string
  tenant_id: string
  email: string
  full_name: string | null
  role: Role
  phone: string | null
  is_active: boolean
  force_password_change: boolean
  last_login_at: string | null
  created_at: string
}

function toUser(raw: RawUser): User {
  return {
    id: raw.id,
    email: raw.email,
    name: raw.full_name || raw.email,
    role: raw.role,
    tenant_id: raw.tenant_id,
    permissions: [],
    is_active: raw.is_active,
    last_login: raw.last_login_at,
    created_at: raw.created_at,
    phone: raw.phone ?? undefined,
    force_password_change: raw.force_password_change,
  }
}

export interface CreateUserPayload {
  email: string
  full_name: string
  role: Role
  phone?: string
  /** Leave undefined to have the backend generate a random temporary password. */
  password?: string
  must_change_password?: boolean
  send_welcome_email?: boolean
}

interface RawCreateUserResponse extends RawUser {
  email_sent: boolean
  temporary_password?: string
}

export interface CreateUserResult extends User {
  email_sent: boolean
  /** Only present when `password` was omitted from the request - shown once. */
  temporary_password?: string
}

export interface ResetPasswordPayload {
  /** Leave undefined to have the backend generate a random temporary password. */
  new_password?: string
  notify_user?: boolean
}

export interface ResetPasswordResult {
  message: string
  user_id: string
  email_sent: boolean
  /** Only present when `new_password` was omitted from the request - shown once. */
  temporary_password?: string
}

export interface AuditLogEntry {
  id: string
  user_id: string | null
  user_email: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  ip_address: string | null
  created_at: string
}

// Matches AuditTrail.query()'s real return shape - {total, limit, offset,
// entries: [...]}, NOT a bare array. The old code typed this as
// AuditLogEntry[] directly, which meant .length / .map on the response
// would have failed at runtime the first time this endpoint was actually
// reachable.
interface RawAuditLogResponse {
  total: number
  limit: number
  offset: number
  entries: AuditLogEntry[]
}

export const usersApi = {
  list: (tenantId: string) =>
    apiClient.get<RawUser[]>(`/tenants/${tenantId}/users`).then((r) => r.data.map(toUser)),

  create: (tenantId: string, payload: CreateUserPayload) =>
    apiClient.post<RawCreateUserResponse>(`/tenants/${tenantId}/users`, payload).then((r) => ({
      ...toUser(r.data),
      email_sent: r.data.email_sent,
      temporary_password: r.data.temporary_password,
    } satisfies CreateUserResult)),

  updateRole: (tenantId: string, userId: string, role: Role) =>
    apiClient.put(`/tenants/${tenantId}/users/${userId}/role`, { role }),

  // Backend deactivates via DELETE (soft-delete: sets is_active = FALSE),
  // not a POST /deactivate action route.
  deactivate: (tenantId: string, userId: string) =>
    apiClient.delete(`/tenants/${tenantId}/users/${userId}`),

  // This endpoint did not exist on the backend at all before - the
  // frontend already had a Reactivate button with nothing to call.
  reactivate: (tenantId: string, userId: string) =>
    apiClient.post(`/tenants/${tenantId}/users/${userId}/reactivate`),

  resetPassword: (tenantId: string, userId: string, payload: ResetPasswordPayload = {}) =>
    apiClient
      .post<ResetPasswordResult>(`/tenants/${tenantId}/users/${userId}/reset-password`, payload)
      .then((r) => r.data),

  auditLog: (params?: { user_id?: string; action?: string; limit?: number; offset?: number }) =>
    apiClient
      .get<RawAuditLogResponse>('/audit/logs', { params })
      .then((r) => r.data.entries),
}
