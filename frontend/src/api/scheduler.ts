import apiClient from './client'
import { ScheduledJob } from '@/types'

export const schedulerApi = {
  list: () => apiClient.get<ScheduledJob[]>('/scheduler/jobs').then((r) => r.data),
  create: (body: Record<string, any>) =>
    apiClient.post<ScheduledJob>('/scheduler/jobs', body).then((r) => r.data),
  update: (id: string, body: Record<string, any>) =>
    apiClient.put<ScheduledJob>(`/scheduler/jobs/${id}`, body).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/scheduler/jobs/${id}`),
  toggle: (id: string, is_active: boolean) =>
    apiClient.patch(`/scheduler/jobs/${id}`, { is_active }),
  runNow: (id: string) => apiClient.post(`/scheduler/jobs/${id}/run-now`),
  history: (id: string) => apiClient.get(`/scheduler/jobs/${id}/history`).then((r) => r.data),
}
