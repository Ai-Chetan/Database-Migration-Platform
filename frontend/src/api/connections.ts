import apiClient from './client'
import { Connection, Engine } from '@/types'

// ── Raw backend shapes ───────────────────────────────────────────────────
// Matches enterprise/routers/connections.py + connection_manager.py exactly
// (verified against backend source).
//
// CHANGE: this whole file previously sent {engine, database} straight
// through from the form to the API, but the backend's fields are
// {db_type, database_name} - RegisterConnectionRequest would 422 on every
// single create ("field required: db_type", "field required:
// database_name"), and the update endpoint didn't exist at all before
// (see connections.py). Fixed both the outgoing field names and added a
// response mapper, same pattern as api/users.ts's toUser().

interface RawConnection {
  id: string
  tenant_id: string
  name: string
  db_type: Engine
  host: string
  port: number
  database_name: string
  username: string
  ssl_enabled: boolean
  connection_pool_size: number
  connect_timeout: number
  query_timeout: number
  last_tested_at: string | null
  last_test_status: 'success' | 'failed' | null
  last_test_error: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

function toConnection(raw: RawConnection): Connection {
  return {
    id: raw.id,
    name: raw.name,
    engine: raw.db_type,
    host: raw.host,
    port: raw.port,
    database: raw.database_name,
    username: raw.username,
    status: raw.last_test_status === 'success' ? 'healthy' : raw.last_test_status === 'failed' ? 'failed' : 'untested',
    last_tested_at: raw.last_tested_at,
    // Only ever known right after a live /test call, not from list/get -
    // the backend doesn't persist latency at rest.
    latency_ms: null,
  }
}

export interface TestResult {
  success: boolean
  latency_ms: number | null
  db_version?: string
  table_count?: number
  error?: string | null
}

export interface CreateConnectionPayload {
  name: string
  engine: Engine
  host: string
  port: number
  database: string
  username: string
  password: string
  test_before_save?: boolean
}

export interface UpdateConnectionPayload {
  name?: string
  host?: string
  port?: number
  database?: string
  username?: string
}

export const connectionsApi = {
  list: () => apiClient.get<RawConnection[]>('/connections').then((r) => r.data.map(toConnection)),

  get: (id: string) => apiClient.get<RawConnection>(`/connections/${id}`).then((r) => toConnection(r.data)),

  create: (payload: CreateConnectionPayload) =>
    apiClient
      .post<RawConnection>('/connections', {
        name: payload.name,
        db_type: payload.engine,
        host: payload.host,
        port: payload.port,
        database_name: payload.database,
        username: payload.username,
        password: payload.password,
        test_before_save: payload.test_before_save ?? true,
      })
      .then((r) => toConnection(r.data)),

  // CHANGE: PUT /connections/{id} did not exist before - every edit
  // 404'd. Does NOT accept a password - see rotatePassword() below.
  update: (id: string, payload: UpdateConnectionPayload) =>
    apiClient
      .put<RawConnection>(`/connections/${id}`, {
        name: payload.name,
        host: payload.host,
        port: payload.port,
        database_name: payload.database,
        username: payload.username,
      })
      .then((r) => toConnection(r.data)),

  // Password changes are a distinct action from other field edits - the
  // backend re-tests the new password before committing it (unlike a
  // generic field update, which doesn't need to touch the live database).
  rotatePassword: (id: string, newPassword: string, testBeforeRotate = true) =>
    apiClient
      .put(`/connections/${id}/rotate`, { new_password: newPassword, test_before_rotate: testBeforeRotate })
      .then((r) => r.data),

  remove: (id: string) => apiClient.delete(`/connections/${id}`),

  test: (id: string) => apiClient.post<TestResult>(`/connections/${id}/test`).then((r) => r.data),

  testRaw: (payload: { engine: Engine; host: string; port: number; database: string; username: string; password: string }) =>
    apiClient
      .post<TestResult>('/connections/test-raw', {
        db_type: payload.engine,
        host: payload.host,
        port: payload.port,
        database_name: payload.database,
        username: payload.username,
        password: payload.password,
      })
      .then((r) => r.data),
}
