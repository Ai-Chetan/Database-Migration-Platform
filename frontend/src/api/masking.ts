import apiClient from './client'
import { MaskingRule } from '@/types'

export const maskingApi = {
  listRules: (projectId: string) =>
    apiClient.get<MaskingRule[]>('/masking/rules', { params: { project_id: projectId } }).then((r) => r.data),
  createRule: (body: Record<string, any>) =>
    apiClient.post<MaskingRule>('/masking/rules', body).then((r) => r.data),
  updateRule: (id: string, body: Record<string, any>) =>
    apiClient.put<MaskingRule>(`/masking/rules/${id}`, body).then((r) => r.data),
  deleteRule: (id: string) => apiClient.delete(`/masking/rules/${id}`),
  suggestRules: (projectId: string) =>
    apiClient.post('/masking/rules/suggest', { project_id: projectId }).then((r) => r.data),
  previewMask: (body: Record<string, any>) =>
    apiClient.post('/masking/preview', body).then((r) => r.data),

  // Synthetic data
  generateSynthetic: (body: Record<string, any>) =>
    apiClient.post('/masking/synthetic/generate', body).then((r) => r.data),
}
