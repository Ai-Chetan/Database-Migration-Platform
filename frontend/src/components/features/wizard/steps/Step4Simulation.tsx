import React, { useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { Sparkles } from 'lucide-react'
import { simulationApi } from '@/api/simulation'
import { Card, Spinner, Badge } from '@/components/common'
import { formatDuration } from '@/utils/format'
import { cn } from '@/utils/cn'
import { WizardState } from '../wizardState'

interface Props {
  state: WizardState
  update: (patch: Partial<WizardState>) => void
}

export function Step4Simulation({ state, update }: Props) {
  const sweepMutation = useMutation({
    mutationFn: () =>
      simulationApi.sweep({ connection_id: state.sourceConnectionId, min_workers: 1, max_workers: 16 }),
    onSuccess: (result) => update({ sweepResult: result, workerCount: result.sweet_spot_workers }),
  })

  useEffect(() => {
    if (!state.sweepResult) sweepMutation.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (sweepMutation.isPending || !state.sweepResult) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Spinner size="lg" />
        <p className="text-body text-text-secondary">Simulating worker counts from 1 to 16…</p>
      </div>
    )
  }

  const result = state.sweepResult
  const chartData = result.sweep.map((p) => ({
    workers: p.worker_count,
    duration_sec: p.estimated_duration_sec,
    failure_pct: p.failure_probability_pct,
  }))

  return (
    <div className="max-w-2xl space-y-5">
      <div className="flex items-center gap-2 rounded border border-action/30 bg-action/5 p-4">
        <Sparkles className="h-4 w-4 shrink-0 text-action" />
        <p className="text-small text-text-secondary">
          <span className="font-semibold text-text-primary">Sweet spot: {result.sweet_spot_workers} workers.</span>{' '}
          {result.sweet_spot_reason}
        </p>
      </div>

      <Card>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
            <XAxis dataKey="workers" tick={{ fontSize: 12 }} label={{ value: 'Workers', position: 'insideBottom', offset: -4, fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => formatDuration(v)} width={70} />
            <Tooltip formatter={(v: number) => formatDuration(v)} labelFormatter={(l) => `${l} workers`} />
            <ReferenceLine x={result.sweet_spot_workers} stroke="#2563EB" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="duration_sec" stroke="#2563EB" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <div className="grid grid-cols-3 gap-3">
        {result.sweep
          .filter((p) => [1, result.sweet_spot_workers, 16].includes(p.worker_count))
          .map((p) => (
            <button
              key={p.worker_count}
              onClick={() => update({ workerCount: p.worker_count })}
              className={cn(
                'rounded border p-3 text-left transition-colors',
                state.workerCount === p.worker_count ? 'border-action bg-action/5' : 'border-border hover:bg-surface'
              )}
            >
              <p className="text-h4 text-text-primary">{p.worker_count} workers</p>
              <p className="text-tiny text-text-secondary">{p.estimated_duration_str}</p>
              <Badge tone={p.failure_probability_pct > 10 ? 'error' : p.failure_probability_pct > 3 ? 'warning' : 'success'} className="mt-2">
                {p.failure_probability_pct.toFixed(1)}% failure risk
              </Badge>
            </button>
          ))}
      </div>
    </div>
  )
}
