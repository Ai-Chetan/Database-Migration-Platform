import React, { useState } from 'react'
import { Worker, WorkerStatus } from '@/types'

const CELL_COLOR: Record<WorkerStatus, string> = {
  BUSY: 'bg-action',
  IDLE: 'bg-slate-600',
  PAUSED: 'bg-warning',
  QUARANTINED: 'bg-error',
  STOPPING: 'bg-warning',
  OFFLINE: 'bg-slate-800',
}

/**
 * Dense heatmap of the entire worker fleet - one cell per worker, colored by
 * status, busy workers pulse. Meant to answer "what is my fleet doing right
 * now" at a glance, the way a Kubernetes node map or Datadog host map does.
 * The DataTable below still owns the actual pause/resume/quarantine actions -
 * this is the overview layer, not a replacement for it.
 */
export function WorkerFleetGrid({ workers }: { workers: Worker[] }) {
  const [hovered, setHovered] = useState<Worker | null>(null)

  if (workers.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-lg bg-sidebar-bg text-small text-sidebar-text">
        No workers registered
      </div>
    )
  }

  return (
    <div className="relative overflow-hidden rounded-lg bg-sidebar-bg p-5">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-small font-medium text-white">Fleet map</p>
        <p className="mono text-tiny text-sidebar-text">{workers.length} workers</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {workers.map((w) => (
          <div
            key={w.worker_id}
            onMouseEnter={() => setHovered(w)}
            onMouseLeave={() => setHovered((h) => (h?.worker_id === w.worker_id ? null : h))}
            className={`h-4 w-4 shrink-0 cursor-default rounded-[3px] ${CELL_COLOR[w.status]} ${
              w.status === 'BUSY' ? 'animate-pulse-dot' : ''
            } transition-transform hover:scale-125`}
          />
        ))}
      </div>

      {hovered && (
        <div className="mt-4 flex items-center gap-4 border-t border-slate-700/60 pt-3 text-tiny">
          <span className="mono text-white">{hovered.worker_id}</span>
          <span className="text-sidebar-text">{hovered.host}</span>
          <span className="text-sidebar-text">pid {hovered.pid}</span>
          {hovered.current_job_id && (
            <span className="mono text-sidebar-text">job {hovered.current_job_id.slice(0, 8)}</span>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-slate-700/60 pt-3">
        {(Object.keys(CELL_COLOR) as WorkerStatus[]).map((status) => (
          <div key={status} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-[2px] ${CELL_COLOR[status]}`} />
            <span className="text-tiny text-sidebar-text">{status.charAt(0) + status.slice(1).toLowerCase()}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
