import React from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { Activity } from 'lucide-react'
import { Card } from '@/components/common'
import { ThroughputSample } from '@/hooks/useLiveThroughput'
import { formatNumber } from '@/utils/format'

function CustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as ThroughputSample
  return (
    <div className="rounded border border-border bg-white px-3 py-2 shadow-sm">
      <p className="mono text-small font-semibold text-text-primary">
        {formatNumber(Math.round(p.rowsPerSec))} rows/sec
      </p>
      <p className="text-tiny text-text-tertiary">{p.activeWorkers} active workers</p>
    </div>
  )
}

export function ThroughputChart({
  series,
  activeJobCount,
}: {
  series: ThroughputSample[]
  activeJobCount: number
}) {
  const latest = series[series.length - 1]

  return (
    <Card padding="none">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <p className="flex items-center gap-2 text-h4 text-text-primary">
            Fleet throughput
            {activeJobCount > 0 && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-success/10 px-2 py-0.5 text-tiny font-medium text-success">
                <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-dot" />
                LIVE
              </span>
            )}
          </p>
          <p className="text-tiny text-text-tertiary">Rows/sec across all running jobs, this session</p>
        </div>
        {latest && (
          <div className="text-right">
            <p className="mono text-h3 tabular-nums text-text-primary">{formatNumber(Math.round(latest.rowsPerSec))}</p>
            <p className="text-tiny text-text-tertiary">rows/sec</p>
          </div>
        )}
      </div>

      <div className="p-4">
        {activeJobCount === 0 ? (
          <div className="flex h-48 flex-col items-center justify-center gap-2 text-center">
            <Activity className="h-6 w-6 text-text-tertiary" />
            <p className="text-small text-text-secondary">No jobs running right now</p>
            <p className="text-tiny text-text-tertiary">Throughput will appear here once a migration starts.</p>
          </div>
        ) : series.length < 2 ? (
          <div className="flex h-48 flex-col items-center justify-center gap-2 text-center">
            <Activity className="h-6 w-6 animate-pulse text-action" />
            <p className="text-small text-text-secondary">Collecting live samples…</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={series} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="throughputFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#2563EB" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="#2563EB" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="t"
                tickFormatter={(t) => new Date(t).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' })}
                tick={{ fontSize: 11, fill: '#94A3B8' }}
                axisLine={{ stroke: '#E2E8F0' }}
                tickLine={false}
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#94A3B8' }}
                axisLine={false}
                tickLine={false}
                width={48}
                tickFormatter={(v) => formatNumber(v)}
              />
              <Tooltip content={<CustomTooltip />} />
              <Area
                type="monotone"
                dataKey="rowsPerSec"
                stroke="#2563EB"
                strokeWidth={2}
                fill="url(#throughputFill)"
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  )
}
