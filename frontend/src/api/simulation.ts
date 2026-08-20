import apiClient from './client'
import { SimulationSweepResult } from '@/types'

// CHANGE: every method in this file called a URL that doesn't exist.
// Real router prefix is /simulate, not /simulation (see
// backend/simulation/routers/simulation.py) - so /simulation/sweep,
// /simulation/scenario, /simulation/scenarios were all 404ing. There is
// also no min_workers/max_workers concept on the backend - worker-sweep
// takes an explicit worker_counts list, and both source_engine/
// target_engine are required (defaulted to mysql here since neither the
// Simulation page nor the wizard step currently collect engine choice).

function workerCountsFromRange(min: number, max: number): number[] {
  // Backend's own default sweep is roughly a doubling sequence
  // ([2,4,8,16,32]) - mirror that shape within the requested range rather
  // than testing every integer (worker_count is capped at 256 and a dense
  // sweep would be slow and not meaningfully more informative).
  const counts: number[] = []
  let w = Math.max(1, min)
  while (w < max) {
    counts.push(w)
    w *= 2
  }
  counts.push(max)
  return Array.from(new Set(counts.map((c) => Math.min(256, Math.max(1, c))))).sort((a, b) => a - b)
}

export const simulationApi = {
  /** Sweeps worker counts between min/max (doubling steps) to find the sweet spot. */
  sweep: (params: { connection_id: string; min_workers?: number; max_workers?: number; source_engine?: string; target_engine?: string }) =>
    apiClient
      .post<SimulationSweepResult>('/simulate/worker-sweep', {
        connection_id: params.connection_id,
        source_engine: params.source_engine ?? 'mysql',
        target_engine: params.target_engine ?? 'mysql',
        worker_counts: workerCountsFromRange(params.min_workers ?? 2, params.max_workers ?? 32),
      })
      .then((r) => r.data),

  /** Runs a single simulation for one worker count / chunk strategy. */
  run: (body: {
    connection_id?: string
    name?: string
    worker_count?: number
    chunk_size_strategy?: string
    source_engine?: string
    target_engine?: string
    manual_tables?: Record<string, any>[]
  }) => apiClient.post('/simulate', body).then((r) => r.data),

  /** Compares named scenarios side by side (fastest first). */
  compare: (body: {
    connection_id: string
    source_engine?: string
    target_engine?: string
    scenarios: { name: string; worker_count: number; chunk_size_strategy?: string }[]
  }) => apiClient.post('/simulate/compare', body).then((r) => r.data),

  listRuns: (params?: { connection_id?: string; limit?: number }) =>
    apiClient
      .get<{ total: number; runs: any[] }>('/simulate/runs', { params })
      .then((r) => r.data.runs),

  getRun: (runId: string) => apiClient.get(`/simulate/runs/${runId}`).then((r) => r.data),
}
