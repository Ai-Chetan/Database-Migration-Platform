import React, { useState } from 'react'
import { formatNumber } from '@/utils/format'

interface Segment {
  label: string
  value: number
  color: string
}

export function ChunkStatusBar({ segments }: { segments: Segment[] }) {
  const total = segments.reduce((s, seg) => s + seg.value, 0) || 1
  const [hovered, setHovered] = useState<string | null>(null)

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface">
        {segments.map((seg) =>
          seg.value > 0 ? (
            <div
              key={seg.label}
              className={`h-full transition-opacity ${seg.color}`}
              style={{ width: `${(seg.value / total) * 100}%`, opacity: hovered && hovered !== seg.label ? 0.35 : 1 }}
              onMouseEnter={() => setHovered(seg.label)}
              onMouseLeave={() => setHovered(null)}
            />
          ) : null
        )}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {segments.map((seg) => (
          <div
            key={seg.label}
            onMouseEnter={() => setHovered(seg.label)}
            onMouseLeave={() => setHovered(null)}
            className="cursor-default"
          >
            <p className="flex items-center gap-1.5 text-tiny text-text-secondary">
              <span className={`h-2 w-2 shrink-0 rounded-[2px] ${seg.color}`} />
              {seg.label}
            </p>
            <p className="mt-0.5 mono text-h4 tabular-nums text-text-primary">{formatNumber(seg.value)}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
