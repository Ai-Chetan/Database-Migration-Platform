import apiClient from './client'
import { SimulationSweepResult } from '@/types'

export const simulationApi = {
  sweep: (body: { connection_id: string; min_workers?: number; max_workers?: number }) =>
    apiClient.post<SimulationSweepResult>('/simulation/sweep', body).then((r) => r.data),

  scenario: (body: Record<string, any>) =>
    apiClient.post('/simulation/scenario', body).then((r) => r.data),

  listScenarios: () => apiClient.get('/simulation/scenarios').then((r) => r.data),
}
