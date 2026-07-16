import apiClient from './client'
import { User, Role } from '@/types'

export interface AuditLogEntry {
  id: string
  user_id: string
  user_email: string
  action: string
  resource_type: string
  resource_id: string | null
  ip_address: string | null
  created_at: string
}

export const usersApi = {
  list: () => apiClient.get<User[]>('/users').then((r) => r.data),
  invite: (email: string, role: Role) =>
    apiClient.post<User>('/users/invite', { email, role }).then((r) => r.data),
  updateRole: (id: string, role: Role) =>
    apiClient.put(`/users/${id}/role`, { role }),
  deactivate: (id: string) => apiClient.post(`/users/${id}/deactivate`),
  reactivate: (id: string) => apiClient.post(`/users/${id}/reactivate`),

  auditLog: (params?: { user_id?: string; action?: string; limit?: number; offset?: number }) =>
    apiClient.get<AuditLogEntry[]>('/audit-log', { params }).then((r) => r.data),
}
