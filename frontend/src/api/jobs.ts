import apiClient from './client'
import { Job, LiveStats } from '@/types'

// ── Raw backend response shape ─────────────────────────────────────────────
// Matches control_plane/app/routers/jobs.py's _job_to_dict() exactly
// (verified against backend source).
//
// CHANGE: this whole file previously passed the raw backend job dict
// straight through as if it were a Job. It isn't - the real record has
// NO name, source_engine, target_engine, progress_pct, or rows_migrated
// fields at all (jobs are created from a raw source_config/target_config
// JSON blob, not named or given a worker count up front). Every page
// reading job.source_engine got undefined, which crashed EngineIcon
// entirely ("Element type is invalid... got: undefined"). This mapper
// derives every UI field from what's actually there.

interface RawJob {
  id: string
  tenant_id: string
  status: string
  source_config: { engine?: string; database?: string; host?: string } | null
  target_config: { engine?: string; database?: string; host?: string } | null
  total_tables: number
  total_chunks: number
  completed_chunks: number
  failed_chunks: number
  created_at: string | null
  started_at: string | null
  completed_at: string | null
  last_error: string | null
}

function toJob(raw: RawJob): Job {
  const sourceDb = raw.source_config?.database || raw.source_config?.host || '?'
  const targetDb = raw.target_config?.database || raw.target_config?.host || '?'
  return {
    id: raw.id,
    // Backend doesn't store a job name - synthesize one from the source/
    // target databases so the UI always has something readable to show.
    name: `${sourceDb} \u2192 ${targetDb}`,
    status: raw.status as Job['status'],
    source_engine: (raw.source_config?.engine || 'mysql') as Job['source_engine'],
    target_engine: (raw.target_config?.engine || 'mysql') as Job['target_engine'],
    source_database: sourceDb,
    target_database: targetDb,
    total_tables: raw.total_tables ?? 0,
    total_chunks: raw.total_chunks ?? 0,
    completed_chunks: raw.completed_chunks ?? 0,
    failed_chunks: raw.failed_chunks ?? 0,
    progress_pct: raw.total_chunks > 0 ? Math.round((raw.completed_chunks / raw.total_chunks) * 1000) / 10 : 0,
    // Not available from this endpoint - the real number lives on
    // GET /ops/jobs/{id}/live-stats (see liveStats() below), which
    // JobDetail.tsx already prefers over this fallback value.
    rows_migrated: 0,
    started_at: raw.started_at,
    completed_at: raw.completed_at,
    error_message: raw.last_error,
    created_at: raw.created_at || new Date().toISOString(),
  }
}

export const jobsApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }) =>
    apiClient
      .get<{ jobs: RawJob[]; total: number }>('/jobs', { params })
      .then((r) => r.data.jobs.map(toJob)),

  get: (id: string) => apiClient.get<RawJob>(`/jobs/${id}`).then((r) => toJob(r.data)),

  create: (body: {
    source_connection_id?: string
    target_connection_id?: string
    source_config?: Record<string, any>
    target_config?: Record<string, any>
  }) => apiClient.post<RawJob>('/jobs', body).then((r) => toJob(r.data)),

  start: (id: string) => apiClient.post(`/jobs/${id}/start`),

  remove: (id: string) => apiClient.delete(`/jobs/${id}`),

  liveStats: (id: string) => apiClient.get<LiveStats>(`/ops/jobs/${id}/live-stats`).then((r) => r.data),
}
