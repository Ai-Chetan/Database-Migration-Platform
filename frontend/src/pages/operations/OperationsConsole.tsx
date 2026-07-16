import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { Pause, Play, ShieldAlert, StopCircle, Cpu, Users2, AlertTriangle } from 'lucide-react'
import { operationsApi } from '@/api/operations'
import { jobsApi } from '@/api/jobs'
import { Worker, TuningAction } from '@/types'
import {
  PageHeader,
  Card,
  Button,
  DataTable,
  WorkerStatusBadge,
  Select,
  Badge,
} from '@/components/common'
import { formatDateTime } from '@/utils/format'
import { usePermission } from '@/hooks/usePermission'

export default function OperationsConsole() {
  const queryClient = useQueryClient()
  const canOperate = usePermission('operations:*')
  const [selectedJobId, setSelectedJobId] = useState<string>('')

  const { data: jobs = [] } = useQuery({
    queryKey: ['jobs', 'active'],
    queryFn: () => jobsApi.list({ status: 'running', limit: 50 }),
  })

  const { data: workers = [], isLoading } = useQuery({
    queryKey: ['ops', 'workers', 'all'],
    queryFn: () => operationsApi.listWorkers(),
    refetchInterval: 5000,
  })

  const { data: tuningActions = [] } = useQuery({
    queryKey: ['ops', 'tuning', selectedJobId],
    queryFn: () => operationsApi.listTuningActions(selectedJobId),
    enabled: !!selectedJobId,
  })

  const workerActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'quarantine' | 'stop' }) => {
      if (action === 'pause') return operationsApi.pauseWorker(id)
      if (action === 'resume') return operationsApi.resumeWorker(id)
      if (action === 'stop') return operationsApi.stopWorker(id)
      return operationsApi.quarantineWorker(id)
    },
    onSuccess: () => {
      toast.success('Worker updated')
      queryClient.invalidateQueries({ queryKey: ['ops', 'workers'] })
    },
    onError: () => toast.error('Action failed'),
  })

  const counts = {
    busy: workers.filter((w) => w.status === 'BUSY').length,
    idle: workers.filter((w) => w.status === 'IDLE').length,
    paused: workers.filter((w) => w.status === 'PAUSED').length,
    quarantined: workers.filter((w) => w.status === 'QUARANTINED').length,
  }

  const columns: ColumnDef<Worker>[] = [
    { header: 'Worker ID', accessorKey: 'worker_id', cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'Job', accessorKey: 'current_job_id', cell: ({ getValue }) => <span className="mono text-small text-text-secondary">{getValue<string | null>()?.slice(0, 8) ?? '—'}</span> },
    { header: 'Host', accessorKey: 'host' },
    { header: 'PID', accessorKey: 'pid' },
    { header: 'Status', accessorKey: 'status', cell: ({ getValue }) => <WorkerStatusBadge status={getValue<Worker['status']>()} /> },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) =>
        canOperate ? (
          <div className="flex justify-end gap-1">
            {row.original.status === 'BUSY' && (
              <Button variant="ghost" size="sm" title="Pause" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'pause' })}>
                <Pause className="h-3.5 w-3.5" />
              </Button>
            )}
            {row.original.status === 'PAUSED' && (
              <Button variant="ghost" size="sm" title="Resume" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'resume' })}>
                <Play className="h-3.5 w-3.5" />
              </Button>
            )}
            {row.original.status !== 'QUARANTINED' && (
              <Button variant="ghost" size="sm" title="Quarantine" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'quarantine' })}>
                <ShieldAlert className="h-3.5 w-3.5 text-warning" />
              </Button>
            )}
            <Button variant="ghost" size="sm" title="Stop" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'stop' })}>
              <StopCircle className="h-3.5 w-3.5 text-error" />
            </Button>
          </div>
        ) : null,
    },
  ]

  return (
    <div>
      <PageHeader title="Operations Console" description="Live control over the worker fleet across all running migrations." />

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card padding="sm">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded bg-sky-50"><Cpu className="h-4 w-4 text-info" /></div>
            <div><p className="text-tiny text-text-secondary">Busy</p><p className="text-h4 text-text-primary">{counts.busy}</p></div>
          </div>
        </Card>
        <Card padding="sm">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded bg-surface"><Users2 className="h-4 w-4 text-text-secondary" /></div>
            <div><p className="text-tiny text-text-secondary">Idle</p><p className="text-h4 text-text-primary">{counts.idle}</p></div>
          </div>
        </Card>
        <Card padding="sm">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded bg-amber-50"><Pause className="h-4 w-4 text-warning" /></div>
            <div><p className="text-tiny text-text-secondary">Paused</p><p className="text-h4 text-text-primary">{counts.paused}</p></div>
          </div>
        </Card>
        <Card padding="sm">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded bg-red-50"><AlertTriangle className="h-4 w-4 text-error" /></div>
            <div><p className="text-tiny text-text-secondary">Quarantined</p><p className="text-h4 text-text-primary">{counts.quarantined}</p></div>
          </div>
        </Card>
      </div>

      <Card padding="none" className="mb-6">
        <div className="border-b border-border px-6 py-4">
          <p className="text-h4 text-text-primary">Worker fleet</p>
        </div>
        <div className="p-4">
          <DataTable columns={columns} data={workers} isLoading={isLoading} emptyMessage="No active workers" />
        </div>
      </Card>

      <Card padding="none">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <p className="text-h4 text-text-primary">Resource governor — tuning log</p>
          <Select className="w-64" value={selectedJobId} onChange={(e) => setSelectedJobId(e.target.value)}>
            <option value="">Select a running job…</option>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="p-4">
          {!selectedJobId ? (
            <p className="p-4 text-center text-body text-text-secondary">Select a job to view its auto-tuning history.</p>
          ) : (
            <DataTable
              columns={[
                { header: 'Action', accessorKey: 'action_type' },
                { header: 'Reason', accessorKey: 'reason' },
                { header: 'Triggered by', accessorKey: 'triggered_by', cell: ({ getValue }) => <Badge tone="neutral">{String(getValue())}</Badge> },
                { header: 'When', accessorKey: 'created_at', cell: ({ getValue }) => formatDateTime(getValue<string>()) },
              ] as ColumnDef<TuningAction>[]}
              data={tuningActions}
              emptyMessage="No auto-tuning actions recorded for this job"
            />
          )}
        </div>
      </Card>
    </div>
  )
}
