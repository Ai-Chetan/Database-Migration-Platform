import apiClient from './client'
import { ReportType } from '@/types'

export interface Report {
  id: string
  report_type: ReportType
  job_id: string | null
  title: string
  format: 'pdf' | 'html' | 'json'
  status: string
  created_at: string
  download_url: string | null
}

export const reportsApi = {
  list: (params?: { report_type?: string; job_id?: string }) =>
    apiClient.get<Report[]>('/reports', { params }).then((r) => r.data),
  generate: (body: { report_type: ReportType; job_id?: string; format: string }) =>
    apiClient.post<Report>('/reports/generate', body).then((r) => r.data),
  get: (id: string) => apiClient.get<Report>(`/reports/${id}`).then((r) => r.data),
  remove: (id: string) => apiClient.delete(`/reports/${id}`),
}
