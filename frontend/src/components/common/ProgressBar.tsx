import React from 'react'
import { cn } from '@/utils/cn'

interface ProgressBarProps {
  value: number // 0-100
  tone?: 'action' | 'success' | 'warning' | 'error'
  size?: 'sm' | 'md'
  showLabel?: boolean
  className?: string
}

const toneClasses = {
  action: 'bg-action',
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
}

export function ProgressBar({ value, tone = 'action', size = 'md', showLabel, className }: ProgressBarProps) {
  const clamped = Math.min(100, Math.max(0, value))
  const height = size === 'sm' ? 'h-1.5' : 'h-2.5'

  return (
    <div className={cn('w-full', className)}>
      <div className={cn('w-full overflow-hidden rounded-full bg-surface', height)}>
        <div
          className={cn('h-full rounded-full transition-all duration-500 ease-out', toneClasses[tone])}
          style={{ width: `${clamped}%` }}
        />
      </div>
      {showLabel && <div className="mt-1 text-tiny tabular-nums text-text-secondary">{clamped.toFixed(1)}%</div>}
    </div>
  )
}
