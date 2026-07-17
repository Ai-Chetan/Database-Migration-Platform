import React, { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceDot,
} from 'recharts'
import { ColumnDef } from '@tanstack/react-table'
import { Sparkles, Gauge, Cpu } from 'lucide-react'
import { connectionsApi } from '@/api/connections'
import { simulationApi } from '@/api/simulation'
import { PageHeader, Card, Select, Button, FormField, Input, Spinner, Badge, EmptyState, DataTable } from '@/components/common'
import { formatDuration } from '@/utils/format'
import { SweepPoint } from '@/types'

interface ChartRow {
  workers: number
  duration_sec: number
  cpu_source: number
  cpu_target: number
}

function ChartTooltip({ active, payload, label, mode }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded border border-border bg-white px-3 py-2 shadow-sm">
      <p className="mono text-tiny text-text-tertiary">{label} workers</p>
      {mode === 'duration' ? (
        <p className="mono mt-0.5 text-small font-semibold text-text-primary">
          {formatDuration(payload[0].value)}
        </p>
      ) : (
        <div className="mt-0.5 space-y-0.5">
          <p className="mono text-small text-warning">source · {payload[0]?.value?.toFixed(0)}%</p>
          <p className="mono text-small text-success">target · {payload[1]?.value?.toFixed(0)}%</p>
        </div>
      )}
    </div>
  )
}

const columns: ColumnDef<SweepPoint, any>[] = [
  {
    accessorKey: 'worker_count',
    header: 'Workers',
    cell: ({ getValue }) => <span className="mono font-medium tabular-nums">{getValue()}</span>,
  },
  { accessorKey: 'estimated_duration_str', header: 'Duration' },
  {
    accessorKey: 'failure_probability_pct',
    header: 'Failure risk',
    cell: ({ getValue }) => {
      const v = getValue() as number
      return (
        <Badge tone={v > 10 ? 'error' : v > 3 ? 'warning' : 'success'}>
          <span className="mono tabular-nums">{v.toFixed(1)}%</span>
        </Badge>
      )
    },
  },
  { accessorKey: 'bottleneck', header: 'Bottleneck', cell: ({ getValue }) => <span className="text-text-secondary">{getValue()}</span> },
]

export default function Simulation() {
  const [connectionId, setConnectionId] = useState('')
  const [minWorkers, setMinWorkers] = useState(1)
  const [maxWorkers, setMaxWorkers] = useState(16)

  const { data: connections = [] } = useQuery({ queryKey: ['connections', 'list'], queryFn: connectionsApi.list })

  const sweepMutation = useMutation({
    mutationFn: () => simulationApi.sweep({ connection_id: connectionId, min_workers: minWorkers, max_workers: maxWorkers }),
  })

  const result = sweepMutation.data

  const chartData = useMemo<ChartRow[] | undefined>(
    () =>
      result?.sweep.map((p) => ({
        workers: p.worker_count,
        duration_sec: p.estimated_duration_sec,
        cpu_source: p.estimated_cpu_source_pct,
        cpu_target: p.estimated_cpu_target_pct,
      })),
    [result]
  )

  const sweetSpotDuration = result && chartData
    ? chartData.find((r) => r.workers === result.sweet_spot_workers)?.duration_sec
    : undefined

  return (
    <div>
      <PageHeader title="Simulation" description="Sweep worker counts to find the fastest, safest configuration before you migrate." />

      <Card className="mb-6 max-w-2xl">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4 sm:items-end">
          <div className="sm:col-span-2">
            <FormField label="Connection">
              <Select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
                <option value="">Select a connection…</option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </FormField>
          </div>
          <FormField label="Min workers">
            <Input type="number" min={1} value={minWorkers} onChange={(e) => setMinWorkers(Number(e.target.value))} />
          </FormField>
          <FormField label="Max workers">
            <Input type="number" min={1} value={maxWorkers} onChange={(e) => setMaxWorkers(Number(e.target.value))} />
          </FormField>
        </div>
        <Button
          leftIcon={<Gauge className="h-4 w-4" />}
          disabled={!connectionId}
          isLoading={sweepMutation.isPending}
          onClick={() => sweepMutation.mutate()}
        >
          Run simulation
        </Button>
      </Card>

      {sweepMutation.isPending && (
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <Spinner size="lg" />
          <p className="text-body text-text-secondary">Running sweep from {minWorkers} to {maxWorkers} workers…</p>
        </div>
      )}

      {!sweepMutation.isPending && !result && (
        <EmptyState icon={Gauge} title="No simulation run yet" description="Choose a connection and run a sweep to see the sweet spot." />
      )}

      {result && chartData && (
        <div className="max-w-3xl space-y-5">
          {/* Sweet-spot insight callout */}
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
            <p className="mb-4 text-small font-semibold text-text-primary">Estimated duration vs. worker count</p>
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="durationFill" x1="0" y1="0" x2="0" y2="1">
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
                <Tooltip content={<ChartTooltip mode="duration" />} />
                <Area type="monotone" dataKey="duration_sec" stroke="none" fill="url(#durationFill)" isAnimationActive={false} />
                <Line type="monotone" dataKey="duration_sec" stroke="#2563EB" strokeWidth={2} dot={false} isAnimationActive={false} />
                {sweetSpotDuration !== undefined && (
                  <ReferenceDot
                    x={result.sweet_spot_workers}
                    y={sweetSpotDuration}
                    r={5}
                    fill="#2563EB"
                    stroke="#FFFFFF"
                    strokeWidth={2}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </Card>

          <Card>
            <p className="mb-1 flex items-center gap-2 text-small font-semibold text-text-primary">
              <Cpu className="h-3.5 w-3.5 text-text-tertiary" />
              Estimated CPU utilization
            </p>
            <div className="mb-3 flex items-center gap-4 text-tiny text-text-tertiary">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-warning" /> Source
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-success" /> Target
              </span>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                <XAxis dataKey="workers" tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={{ stroke: '#E2E8F0' }} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} unit="%" width={40} />
                <Tooltip content={<ChartTooltip mode="cpu" />} />
                <Line type="monotone" dataKey="cpu_source" stroke="#D97706" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="cpu_target" stroke="#16A34A" strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Card>

          <div>
            <p className="mb-3 text-small font-semibold text-text-primary">Full sweep results</p>
            <DataTable columns={columns} data={result.sweep} emptyMessage="No sweep data" />
          </div>
        </div>
      )}
    </div>
  )
}
