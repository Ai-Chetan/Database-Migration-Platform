import { useQuery } from '@tanstack/react-query'
import { jobsApi } from '@/api/jobs'
import { JobStatus } from '@/types'

const TERMINAL_STATUSES: JobStatus[] = ['completed', 'failed', 'cancelled']

/**
 * Polls /ops/jobs/{id}/live-stats every 2s while the job is active,
 * and automatically stops polling once the job reaches a terminal state.
 */
export function useLiveJobStats(jobId: string | undefined, options?: { intervalMs?: number }) {
  const intervalMs = options?.intervalMs ?? 2000

  return useQuery({
    queryKey: ['jobs', jobId, 'live-stats'],
    queryFn: () => jobsApi.liveStats(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && TERMINAL_STATUSES.includes(status)) return false
      return intervalMs
    },
  })
}
