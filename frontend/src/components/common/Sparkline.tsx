import React from 'react'
import { AreaChart, Area, ResponsiveContainer } from 'recharts'
import { Sample } from '@/hooks/useLiveSeries'

export function Sparkline({ data, color = '#2563EB', height = 32 }: { data: Sample[]; color?: string; height?: number }) {
  if (data.length < 2) return <div style={{ height }} />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`spark-${color}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#spark-${color})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
