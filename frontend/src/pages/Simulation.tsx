import React, { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { Sparkles, Gauge } from 'lucide-react'
import { connectionsApi } from '@/api/connections'
import { simulationApi } from '@/api/simulation'
import { PageHeader, Card, Select, Button, FormField, Input, Spinner, Badge, EmptyState } from '@/components/common'
import { formatDuration } from '@/utils/format'

export default function Simulation() {
  const [connectionId, setConnectionId] = useState('')
  const [minWorkers, setMinWorkers] = useState(1)
  const [maxWorkers, setMaxWorkers] = useState(16)

  const { data: connections = [] } = useQuery({ queryKey: ['connections', 'list'], queryFn: connectionsApi.list })

  const sweepMutation = useMutation({
    mutationFn: () => simulationApi.sweep({ connection_id: connectionId, min_workers: minWorkers, max_workers: maxWorkers }),
  })

  const result = sweepMutation.data
  const chartData = result?.sweep.map((p) => ({
    workers: p.worker_count,
    duration_sec: p.estimated_duration_sec,
    cpu_source: p.estimated_cpu_source_pct,
    cpu_target: p.estimated_cpu_target_pct,
  }))

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
          <div className="flex items-center gap-2 rounded border border-action/30 bg-action/5 p-4">
            <Sparkles className="h-4 w-4 shrink-0 text-action" />
            <p className="text-small text-text-secondary">
              <span className="font-semibold text-text-primary">Sweet spot: {result.sweet_spot_workers} workers.</span>{' '}
              {result.sweet_spot_reason}
            </p>
          </div>

          <Card>
            <p className="mb-4 text-small font-semibold text-text-primary">Estimated duration vs. worker count</p>
            <ResponsiveContainer width="100%" height={260}>
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

          <Card>
            <p className="mb-4 text-small font-semibold text-text-primary">Estimated CPU utilization</p>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                <XAxis dataKey="workers" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} unit="%" />
                <Tooltip />
                <Line type="monotone" dataKey="cpu_source" name="Source CPU" stroke="#D97706" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="cpu_target" name="Target CPU" stroke="#16A34A" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <div className="overflow-hidden rounded border border-border">
            <table className="w-full text-left text-small">
              <thead className="bg-surface text-tiny uppercase text-text-secondary">
                <tr>
                  <th className="px-4 py-2">Workers</th>
                  <th className="px-4 py-2">Duration</th>
                  <th className="px-4 py-2">Failure risk</th>
                  <th className="px-4 py-2">Bottleneck</th>
                </tr>
              </thead>
              <tbody>
                {result.sweep.map((p) => (
                  <tr key={p.worker_count} className="border-t border-border">
                    <td className="px-4 py-2 font-medium">{p.worker_count}</td>
                    <td className="px-4 py-2">{p.estimated_duration_str}</td>
                    <td className="px-4 py-2">
                      <Badge tone={p.failure_probability_pct > 10 ? 'error' : p.failure_probability_pct > 3 ? 'warning' : 'success'}>
                        {p.failure_probability_pct.toFixed(1)}%
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-text-secondary">{p.bottleneck}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
