import { useEffect, useRef, useState } from 'react'

export interface Sample {
  t: number
  value: number
}

/**
 * Samples `currentValue` on a fixed interval into a capped rolling window.
 * Genuinely real data (whatever the caller's live query currently holds),
 * just decoupled from the query's own refetch timing so the series updates
 * on a predictable cadence. Used for small in-page sparklines.
 */
export function useLiveSeries(
  currentValue: number | undefined,
  { intervalMs = 2000, maxSamples = 30, active = true }: { intervalMs?: number; maxSamples?: number; active?: boolean } = {}
) {
  const [series, setSeries] = useState<Sample[]>([])
  const ref = useRef(currentValue)
  ref.current = currentValue

  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      if (ref.current === undefined) return
      setSeries((prev) => {
        const next = [...prev, { t: Date.now(), value: ref.current as number }]
        return next.length > maxSamples ? next.slice(next.length - maxSamples) : next
      })
    }, intervalMs)
    return () => clearInterval(id)
  }, [intervalMs, active, maxSamples])

  return series
}
