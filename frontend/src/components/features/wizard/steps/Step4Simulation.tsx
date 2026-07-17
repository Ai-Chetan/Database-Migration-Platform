import React, { useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts'
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

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded border border-border bg-white px-3 py-2 shadow-sm">
      <p className="mono text-tiny text-text-tertiary">{label} workers</p>
      <p className="mono mt-0.5 text-small font-semibold text-text-primary">{formatDuration(payload[0].value)}</p>
    </div>
  )
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
  const sweetSpotDuration = chartData.find((r) => r.workers === result.sweet_spot_workers)?.duration_sec

  return (
    <div className="max-w-2xl space-y-5">
      <div className="relative overflow-hidden rounded-lg bg-sidebar-bg p-5">
        <div
          className="pointer-events-none absolute inset-0"
          style={{ background: 'radial-gradient(circle at 15% 30%, rgba(37,99,235,0.3) 0%, transparent 60%)' }}
        />
        <div className="relative flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-action/20">
            <Sparkles className="h-4 w-4 text-action" />
          </div>
          <div>
            <p className="text-tiny uppercase tracking-wide text-sidebar-text">Recommended configuration</p>
            <p className="mt-0.5 flex items-baseline gap-2">
              <span className="mono text-h2 tabular-nums text-white">{result.sweet_spot_workers}</span>
              <span className="text-small text-sidebar-text">workers</span>
            </p>
            <p className="mt-1 max-w-lg text-small text-sidebar-text">{result.sweet_spot_reason}</p>
          </div>
        </div>
      </div>

      <Card>
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="wizardDurationFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#2563EB" stopOpacity={0.22} />
                <stop offset="100%" stopColor="#2563EB" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
            <XAxis
              dataKey="workers"
              tick={{ fontSize: 11, fill: '#94A3B8' }}
              axisLine={{ stroke: '#E2E8F0' }}
              tickLine={false}
              label={{ value: 'Workers', position: 'insideBottom', offset: -4, fontSize: 11, fill: '#94A3B8' }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#94A3B8' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => formatDuration(v)}
              width={70}
            />
            <Tooltip content={<ChartTooltip />} />
            <Area type="monotone" dataKey="duration_sec" stroke="none" fill="url(#wizardDurationFill)" isAnimationActive={false} />
            <Line type="monotone" dataKey="duration_sec" stroke="#2563EB" strokeWidth={2} dot={false} isAnimationActive={false} />
            {sweetSpotDuration !== undefined && (
              <ReferenceDot x={result.sweet_spot_workers} y={sweetSpotDuration} r={5} fill="#2563EB" stroke="#FFFFFF" strokeWidth={2} />
            )}
          </ComposedChart>
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
              <p className="mono text-h4 tabular-nums text-text-primary">{p.worker_count} workers</p>
              <p className="text-tiny text-text-secondary">{p.estimated_duration_str}</p>
              <Badge tone={p.failure_probability_pct > 10 ? 'error' : p.failure_probability_pct > 3 ? 'warning' : 'success'} className="mt-2">
                <span className="mono tabular-nums">{p.failure_probability_pct.toFixed(1)}%</span>&nbsp;failure risk
              </Badge>
            </button>
          ))}
      </div>
    </div>
  )
}
