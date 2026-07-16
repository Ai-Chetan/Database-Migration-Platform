import apiClient from './client'
import { Project, MappingTable, MappingColumn } from '@/types'

export const schemaApi = {
  // Projects
  listProjects: () => apiClient.get<Project[]>('/schema/projects').then((r) => r.data),
  createProject: (body: Record<string, any>) =>
    apiClient.post<Project>('/schema/projects', body).then((r) => r.data),

  // Discovery
  discover: (projectId: string, connectionId: string) =>
    apiClient
      .post(`/schema/projects/${projectId}/discover`, { connection_id: connectionId })
      .then((r) => r.data),

  // Tables
  listTables: (projectId: string) =>
    apiClient.get<MappingTable[]>(`/schema/projects/${projectId}/tables`).then((r) => r.data),
  autoMapTables: (projectId: string) =>
    apiClient.post(`/schema/projects/${projectId}/tables/auto-map`).then((r) => r.data),

  // Columns / mappings
  listColumns: (tableId: string) =>
    apiClient.get<MappingColumn[]>(`/schema/tables/${tableId}/columns`).then((r) => r.data),
  updateColumn: (columnId: string, body: Record<string, any>) =>
    apiClient.put<MappingColumn>(`/schema/columns/${columnId}`, body).then((r) => r.data),

  // Comparison & datatype engine
  compareSchemas: (projectId: string) =>
    apiClient.get(`/schema/projects/${projectId}/comparison`).then((r) => r.data),
  suggestTypeConversion: (sourceType: string, sourceEngine: string, targetEngine: string) =>
    apiClient
      .get('/schema/datatype/suggest', {
        params: { source_type: sourceType, source_engine: sourceEngine, target_engine: targetEngine },
      })
      .then((r) => r.data),

  // Recommendations
  getRecommendations: (projectId: string) =>
    apiClient.get(`/schema/projects/${projectId}/recommendations`).then((r) => r.data),

  // Validation
  validateMappings: (projectId: string) =>
    apiClient.post(`/schema/projects/${projectId}/validate`).then((r) => r.data),

  // Script generation
  generateScript: (projectId: string) =>
    apiClient.post(`/schema/projects/${projectId}/generate-script`).then((r) => r.data),
  getPlan: (projectId: string) =>
    apiClient.get(`/schema/projects/${projectId}/plan`).then((r) => r.data),

  // Versioning
  listVersions: (projectId: string) =>
    apiClient.get(`/schema/projects/${projectId}/versions`).then((r) => r.data),
}
