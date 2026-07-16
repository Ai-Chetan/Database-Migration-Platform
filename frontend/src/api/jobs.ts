import apiClient from './client'
import { Job, LiveStats } from '@/types'

export const jobsApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }) =>
    apiClient.get<Job[]>('/jobs', { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<Job>(`/jobs/${id}`).then((r) => r.data),
  create: (body: Record<string, any>) => apiClient.post<Job>('/jobs', body).then((r) => r.data),
  start: (id: string) => apiClient.post(`/jobs/${id}/start`),
  remove: (id: string) => apiClient.delete(`/jobs/${id}`),
  liveStats: (id: string) => apiClient.get<LiveStats>(`/ops/jobs/${id}/live-stats`).then((r) => r.data),
}
