import { useEffect, useRef, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { jobsApi } from '@/api/jobs'
import { Job } from '@/types'

export interface ThroughputSample {
  t: number // epoch ms
  rowsPerSec: number
  activeWorkers: number
}

const MAX_SAMPLES = 30 // ~2.5 minutes at a 5s poll interval
const POLL_MS = 5000

/**
 * Aggregates real-time rows/sec and active-worker counts across every
 * currently running job. This is genuinely live data (polled from
 * /ops/jobs/:id/live-stats), accumulated client-side as the dashboard stays
 * open - not synthesized history. The series starts empty on page load and
 * fills in as real samples arrive.
 */
export function useLiveThroughput(runningJobs: Job[]) {
  const [series, setSeries] = useState<ThroughputSample[]>([])
  const lastTick = useRef<number>(0)

  const jobIds = runningJobs.map((j) => j.id)

  const results = useQueries({
    queries: jobIds.map((id) => ({
      queryKey: ['jobs', id, 'live-stats', 'dashboard'],
      queryFn: () => jobsApi.liveStats(id),
      refetchInterval: POLL_MS,
      enabled: jobIds.length > 0,
    })),
  })

  useEffect(() => {
    if (jobIds.length === 0) return
    const now = Date.now()
    // Avoid double-appending when multiple queries resolve within the same tick
    if (now - lastTick.current < POLL_MS - 500) return
    if (results.some((r) => r.isLoading)) return

    lastTick.current = now
    const rowsPerSec = results.reduce((sum, r) => sum + (r.data?.rows_per_sec ?? 0), 0)
    const activeWorkers = results.reduce((sum, r) => sum + (r.data?.active_workers ?? 0), 0)

    setSeries((prev) => {
      const next = [...prev, { t: now, rowsPerSec, activeWorkers }]
      return next.length > MAX_SAMPLES ? next.slice(next.length - MAX_SAMPLES) : next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results.map((r) => r.dataUpdatedAt).join(',')])

  return series
}
