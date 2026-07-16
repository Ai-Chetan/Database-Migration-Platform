import apiClient from './client'
import { Assessment } from '@/types'

export const intelligenceApi = {
  assess: (connectionId: string) =>
    apiClient
      .post<Assessment>('/intelligence/assess', { connection_id: connectionId })
      .then((r) => r.data),

  scanMetadata: (connectionId: string) =>
    apiClient
      .post(`/intelligence/scan`, { connection_id: connectionId })
      .then((r) => r.data),

  getScanResult: (scanId: string) =>
    apiClient.get(`/intelligence/scans/${scanId}`).then((r) => r.data),

  getDistributionStats: (connectionId: string, table: string) =>
    apiClient
      .get(`/intelligence/distribution`, { params: { connection_id: connectionId, table } })
      .then((r) => r.data),

  getRelationships: (connectionId: string) =>
    apiClient
      .get(`/intelligence/relationships`, { params: { connection_id: connectionId } })
      .then((r) => r.data),

  dataQualityScan: (connectionId: string) =>
    apiClient
      .post('/intelligence/data-quality/scan', { connection_id: connectionId })
      .then((r) => r.data),

  estimateCost: (jobConfig: Record<string, any>) =>
    apiClient.post('/intelligence/estimate-cost', jobConfig).then((r) => r.data),
}
