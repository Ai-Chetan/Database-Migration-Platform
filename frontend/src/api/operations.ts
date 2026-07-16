import apiClient from './client'
import { Worker, DriftEvent, TuningAction } from '@/types'

export const operationsApi = {
  // Workers
  listWorkers: (jobId?: string) =>
    apiClient.get<Worker[]>('/ops/workers', { params: jobId ? { job_id: jobId } : {} }).then((r) => r.data),
  pauseWorker: (workerId: string) => apiClient.post(`/ops/workers/${workerId}/pause`),
  resumeWorker: (workerId: string) => apiClient.post(`/ops/workers/${workerId}/resume`),
  quarantineWorker: (workerId: string) => apiClient.post(`/ops/workers/${workerId}/quarantine`),
  stopWorker: (workerId: string) => apiClient.post(`/ops/workers/${workerId}/stop`),

  // Chunks
  listChunks: (jobId: string, params?: { status?: string; limit?: number; offset?: number }) =>
    apiClient.get(`/ops/jobs/${jobId}/chunks`, { params }).then((r) => r.data),
  retryChunk: (chunkId: string) => apiClient.post(`/ops/chunks/${chunkId}/retry`),
  skipChunk: (chunkId: string) => apiClient.post(`/ops/chunks/${chunkId}/skip`),

  // Job control
  pauseJob: (jobId: string) => apiClient.post(`/ops/jobs/${jobId}/pause`),
  resumeJob: (jobId: string) => apiClient.post(`/ops/jobs/${jobId}/resume`),
  cancelJob: (jobId: string) => apiClient.post(`/ops/jobs/${jobId}/cancel`),
  rollbackJob: (jobId: string) => apiClient.post(`/ops/jobs/${jobId}/rollback`),

  // Drift & resource governor
  listDriftEvents: (jobId: string) =>
    apiClient.get<DriftEvent[]>(`/ops/jobs/${jobId}/drift`).then((r) => r.data),
  listTuningActions: (jobId: string) =>
    apiClient.get<TuningAction[]>(`/ops/jobs/${jobId}/tuning`).then((r) => r.data),

  // Dependency graph
  getDependencyGraph: (jobId: string) =>
    apiClient.get(`/ops/jobs/${jobId}/dependency-graph`).then((r) => r.data),
}
