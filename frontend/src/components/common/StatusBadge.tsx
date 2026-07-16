import React from 'react'
import { cn } from '@/utils/cn'
import { JobStatus, WorkerStatus } from '@/types'
import { JOB_STATUS_META, WORKER_STATUS_META } from '@/utils/meta'

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const meta = JOB_STATUS_META[status]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill px-2 py-0.5 text-tiny font-medium', meta.color)}>
      <span
        className={cn('h-1.5 w-1.5 rounded-full', meta.dot, status === 'running' && 'animate-pulse-dot')}
      />
      {meta.label}
    </span>
  )
}

export function WorkerStatusBadge({ status }: { status: WorkerStatus }) {
  const meta = WORKER_STATUS_META[status]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-pill px-2 py-0.5 text-tiny font-medium', meta.color)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', status === 'BUSY' && 'animate-pulse-dot')} style={{ backgroundColor: 'currentColor' }} />
      {meta.label}
    </span>
  )
}
