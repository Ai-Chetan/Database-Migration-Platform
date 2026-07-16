import apiClient from './client'
import { Connection } from '@/types'

export interface TestResult {
  success: boolean
  latency_ms: number | null
  db_version?: string
  table_count?: number
  error?: string | null
}

export const connectionsApi = {
  list: () => apiClient.get<Connection[]>('/connections').then((r) => r.data),
  get: (id: string) => apiClient.get<Connection>(`/connections/${id}`).then((r) => r.data),
  create: (body: Record<string, any>) =>
    apiClient.post<Connection>('/connections', body).then((r) => r.data),
  update: (id: string, body: Record<string, any>) =>
    apiClient.put<Connection>(`/connections/${id}`, body).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/connections/${id}`),
  test: (id: string) => apiClient.post<TestResult>(`/connections/${id}/test`).then((r) => r.data),
}
