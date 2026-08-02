import apiClient from './client'
import { User, Role } from '@/types'

// ── Raw backend response shapes ────────────────────────────────────────────
// These match enterprise/routers/auth.py exactly (verified directly against
// the backend source, not assumed). The backend was previously untouched by
// this audit and is the source of truth here - the frontend was wrong, not
// the backend.

interface RawAuthUser {
  id: string
  email: string
  full_name?: string | null
  role: Role
  tenant_id: string
  permissions?: string[]
}

interface RawTokenResponse {
  token: string
  token_type: string
  expires_in?: number
  user: RawAuthUser
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user: User
}

function toUser(raw: RawAuthUser): User {
  return {
    id: raw.id,
    email: raw.email,
    name: raw.full_name || raw.email,
    role: raw.role,
    tenant_id: raw.tenant_id,
    permissions: raw.permissions ?? [],
  }
}

function toLoginResponse(raw: RawTokenResponse): LoginResponse {
  return {
    access_token: raw.token,
    token_type: raw.token_type,
    user: toUser(raw.user),
  }
}

export interface RegisterPayload {
  tenant_name: string
  tenant_slug: string
  full_name: string
  email: string
  password: string
  plan_name?: string
}

export const authApi = {
  login: (email: string, password: string) =>
    apiClient
      .post<RawTokenResponse>('/auth/login', { email, password })
      .then((r) => toLoginResponse(r.data)),

  register: (payload: RegisterPayload) =>
    apiClient
      .post<RawTokenResponse>('/auth/register', payload)
      .then((r) => toLoginResponse(r.data)),

  logout: () => apiClient.post('/auth/logout'),

  me: () =>
    apiClient.get<{
      user_id: string
      email: string
      role: Role
      tenant_id: string
      permissions: string[]
      full_name: string | null
    }>('/auth/me').then((r) => ({
      id: r.data.user_id,
      email: r.data.email,
      name: r.data.full_name || r.data.email,
      role: r.data.role,
      tenant_id: r.data.tenant_id,
      permissions: r.data.permissions ?? [],
    } satisfies User)),

  changePassword: (current_password: string, new_password: string) =>
    apiClient.post('/auth/change-password', { current_password, new_password }),
}
