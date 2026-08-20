import apiClient from './client'
import { Worker, DriftEvent, TuningAction } from '@/types'

export const operationsApi = {
  // Workers
  // CHANGE: GET /ops/workers has no job_id filter param at all on the
  // backend - passing job_id here was silently ignored, so
  // JobDetail.tsx's "Workers" tab was showing every worker across every
  // job, not just this job's. Per-job listing is a genuinely different
  // endpoint: GET /ops/workers/{job_id}/job.
  listWorkers: () =>
    apiClient
      .get<{ workers: Worker[]; total: number }>('/ops/workers')
      .then((r) => r.data.workers),
  listWorkersForJob: (jobId: string) =>
    apiClient
      .get<{ job_id: string; worker_count: number; workers: Worker[] }>(`/ops/workers/${jobId}/job`)
      .then((r) => r.data.workers),
  pauseWorker: (workerId: string, reason = 'Paused from Operations Console') =>
    apiClient.post(`/ops/workers/${workerId}/pause`, { reason }),
  resumeWorker: (workerId: string, reason = 'Resumed from Operations Console') =>
    apiClient.post(`/ops/workers/${workerId}/resume`, { reason }),
  // CHANGE: real endpoint is /kill, not /stop (that path doesn't exist).
  // Also, the backend rejects kill/quarantine with 400 if reason is empty -
  // the old code sent no body at all, which would 422 before even reaching
  // that check. A default reason is provided so this works immediately;
  // ideally the UI prompts the operator for a real reason (worth a small
  // follow-up), since these are audit-logged operator actions.
  stopWorker: (workerId: string, reason = 'Stopped from Operations Console') =>
    apiClient.post(`/ops/workers/${workerId}/kill`, { reason }),
  quarantineWorker: (workerId: string, reason = 'Quarantined from Operations Console') =>
    apiClient.post(`/ops/workers/${workerId}/quarantine`, { reason }),
  scaleJobWorkers: (jobId: string, targetCount: number, reason = '') =>
    apiClient.post(`/ops/jobs/${jobId}/workers/scale`, { target_count: targetCount, reason }),
  drainJobWorkers: (jobId: string, reason = '') =>
    apiClient.post(`/ops/jobs/${jobId}/workers/drain`, { reason }),

  // Chunks
  // CHANGE: real path is /jobs/{id}/chunks (monitoring_service, prefix
  // /jobs - NOT /ops/jobs), and it only supports a `status` filter, no
  // limit/offset (those were being sent and silently ignored before).
  // Response is {job_id, total, chunks: [...]}, not a bare array.
  listChunks: (jobId: string, params?: { status?: string }) =>
    apiClient
      .get<{ job_id: string; total: number; chunks: any[] }>(`/jobs/${jobId}/chunks`, { params })
      .then((r) => r.data.chunks),
  // Operator triage view: failed / stale / high-retry chunks only.
  listProblemChunks: (jobId: string, limit = 50) =>
    apiClient
      .get<{ job_id: string; total: number; chunks: any[] }>(`/ops/jobs/${jobId}/chunks/problems`, { params: { limit } })
      .then((r) => r.data.chunks),
  getChunkDetail: (chunkId: string) => apiClient.get(`/ops/chunks/${chunkId}`).then((r) => r.data),
  reassignChunk: (chunkId: string, targetWorker: string | null, reason = '') =>
    apiClient.post(`/ops/chunks/${chunkId}/reassign`, { target_worker: targetWorker, reason }),
  // CHANGE: both require a JSON body (ActionRequest) even though `reason`
  // itself is optional server-side - sending literally no body fails
  // FastAPI's request validation before it even gets to read `reason`.
  retryChunk: (chunkId: string, reason = '') => apiClient.post(`/ops/chunks/${chunkId}/retry`, { reason }),
  skipChunk: (chunkId: string, reason = '') => apiClient.post(`/ops/chunks/${chunkId}/skip`, { reason }),

  // Job control (Operations Console's version - draining workers, distinct
  // from the simpler pause/resume/cancel on the Jobs resource itself at
  // /jobs/{id}/pause etc. in jobsApi... which doesn't currently exist as
  // a separate set of methods; this is the one actually wired to the UI).
  // CHANGE: same as workers - these require an ActionRequest body.
  // cancel_job additionally 400s server-side if reason is empty.
  pauseJob: (jobId: string, reason = '') => apiClient.post(`/ops/jobs/${jobId}/pause`, { reason }),
  resumeJob: (jobId: string, reason = '') => apiClient.post(`/ops/jobs/${jobId}/resume`, { reason }),
  cancelJob: (jobId: string, reason: string) => apiClient.post(`/ops/jobs/${jobId}/cancel`, { reason }),
  rollbackJob: (jobId: string) => apiClient.post(`/ops/jobs/${jobId}/rollback`),

  // Drift & resource governor
  // NOTE: no backend implementation exists anywhere in the codebase for
  // schema-drift-detection or self-tuning (the "Live Intelligence Engine"
  // described in the v2 doc is a planned/vision feature, not built yet -
  // confirmed by searching the whole backend for any drift/tuning router).
  // These calls will 404. Left in place (rather than silently faked) so
  // the Drift & Tuning tab visibly needs backend work before shipping,
  // instead of quietly always showing "no drift detected."
  listDriftEvents: (jobId: string) =>
    apiClient.get<DriftEvent[]>(`/ops/jobs/${jobId}/drift`).then((r) => r.data),
  listTuningActions: (jobId: string) =>
    apiClient.get<TuningAction[]>(`/ops/jobs/${jobId}/tuning`).then((r) => r.data),

  // Dependency graph
  // CHANGE: real prefix is /jobs, not /ops/jobs - fixed. This router also
  // wasn't mounted in main.py at all until this fix, so it was 404ing
  // either way before now.
  buildDependencyGraph: (jobId: string, sourceSchema: Record<string, any>, tableNames?: string[]) =>
    apiClient
      .post(`/jobs/${jobId}/dependency-graph`, { source_schema: sourceSchema, table_names: tableNames })
      .then((r) => r.data),
  getDependencyGraph: (jobId: string) =>
    apiClient.get(`/jobs/${jobId}/dependency-graph`).then((r) => r.data),
  getExecutionOrder: (jobId: string) =>
    apiClient.get(`/jobs/${jobId}/dependency-graph/order`).then((r) => r.data),

  // Rollback engine
  // CHANGE: was a single vague rollbackJob(jobId) call to a path that
  // didn't exist (/ops/jobs/{id}/rollback) and wasn't wired to any UI
  // button anyway. The real backend is a deliberate 4-step safety flow -
  // generate a plan BEFORE migrating, dry-run it to preview the exact SQL,
  // and only then execute (irreversible). Replaced with one method per
  // step rather than a single action, so no future UI can accidentally
  // wire a single click straight to an irreversible destructive operation.
  generateRollbackPlan: (jobId: string, migrationPlan: Record<string, any>, tableStates?: Record<string, 'empty' | 'had_data'>) =>
    apiClient
      .post(`/jobs/${jobId}/rollback/generate`, { migration_plan: migrationPlan, table_states: tableStates })
      .then((r) => r.data),
  getRollbackPlan: (jobId: string) =>
    apiClient.get(`/jobs/${jobId}/rollback/plan`).then((r) => r.data),
  dryRunRollback: (jobId: string, targetConnectionId?: string) =>
    apiClient
      .post(`/jobs/${jobId}/rollback/dry-run`, { target_connection_id: targetConnectionId })
      .then((r) => r.data),
  /** IRREVERSIBLE. Require an explicit confirm step in the UI before calling this. */
  executeRollback: (jobId: string, targetConnectionId?: string) =>
    apiClient
      .post(`/jobs/${jobId}/rollback/execute`, { target_connection_id: targetConnectionId })
      .then((r) => r.data),
  getRollbackLog: (jobId: string) =>
    apiClient.get(`/jobs/${jobId}/rollback/log`).then((r) => r.data),
}
