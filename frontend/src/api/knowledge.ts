import apiClient from './client'
import { KnowledgeEntry } from '@/types'

export const knowledgeApi = {
  list: (params?: { entry_type?: string; source_engine?: string; target_engine?: string; search?: string }) =>
    apiClient.get<KnowledgeEntry[]>('/knowledge-base/entries', { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<KnowledgeEntry>(`/knowledge-base/entries/${id}`).then((r) => r.data),
  create: (body: Record<string, any>) =>
    apiClient.post<KnowledgeEntry>('/knowledge-base/entries', body).then((r) => r.data),
  rate: (id: string, usefulness_score: number) =>
    apiClient.post(`/knowledge-base/entries/${id}/rate`, { usefulness_score }),
}
