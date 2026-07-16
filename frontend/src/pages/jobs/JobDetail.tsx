import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import {
  Pause,
  Play,
  XCircle,
  RotateCcw,
  SkipForward,
  ShieldAlert,
  ArrowLeft,
  Activity,
  Users2,
  Timer,
  Database,
} from 'lucide-react'
import { jobsApi } from '@/api/jobs'
import { operationsApi } from '@/api/operations'
import { useLiveJobStats } from '@/hooks/useLiveJobStats'
import { usePermission } from '@/hooks/usePermission'
import {
  PageHeader,
  Button,
  Card,
  JobStatusBadge,
  WorkerStatusBadge,
  ProgressBar,
  Tabs,
  DataTable,
  ConfirmDialog,
  FullPageSpinner,
  Badge,
} from '@/components/common'
import { EngineIcon } from '@/components/common'
import { formatNumber, formatDuration } from '@/utils/format'
import { Worker, DriftEvent } from '@/types'

function StatTile({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <Card padding="sm">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded bg-action/10">
          <Icon className="h-4 w-4 text-action" />
        </div>
        <div>
          <p className="text-tiny text-text-secondary">{label}</p>
          <p className="text-h4 text-text-primary">{value}</p>
        </div>
      </div>
    </Card>
  )
}

export default function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canOperate = usePermission('operations:*')
  const [tab, setTab] = useState('overview')
  const [confirmAction, setConfirmAction] = useState<'pause' | 'resume' | 'cancel' | null>(null)

  const { data: job, isLoading: jobLoading } = useQuery({
    queryKey: ['jobs', jobId],
    queryFn: () => jobsApi.get(jobId as string),
    enabled: !!jobId,
  })

  const { data: liveStats } = useLiveJobStats(jobId)

  const { data: workers = [] } = useQuery({
    queryKey: ['ops', 'workers', jobId],
    queryFn: () => operationsApi.listWorkers(jobId),
    enabled: !!jobId,
    refetchInterval: 5000,
  })

  const { data: chunks = [] } = useQuery({
    queryKey: ['ops', 'chunks', jobId],
    queryFn: () => operationsApi.listChunks(jobId as string, { limit: 100 }),
    enabled: !!jobId && tab === 'chunks',
    refetchInterval: 5000,
  })

  const { data: driftEvents = [] } = useQuery({
    queryKey: ['ops', 'drift', jobId],
    queryFn: () => operationsApi.listDriftEvents(jobId as string),
    enabled: !!jobId && tab === 'drift',
  })

  const controlMutation = useMutation({
    mutationFn: (action: 'pause' | 'resume' | 'cancel') => {
      if (action === 'pause') return operationsApi.pauseJob(jobId as string)
      if (action === 'resume') return operationsApi.resumeJob(jobId as string)
      return operationsApi.cancelJob(jobId as string)
    },
    onSuccess: (_, action) => {
      toast.success(`Job ${action === 'pause' ? 'paused' : action === 'resume' ? 'resumed' : 'cancelled'}`)
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      setConfirmAction(null)
    },
    onError: () => toast.error('Action failed'),
  })

  const workerActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'quarantine' }) => {
      if (action === 'pause') return operationsApi.pauseWorker(id)
      if (action === 'resume') return operationsApi.resumeWorker(id)
      return operationsApi.quarantineWorker(id)
    },
    onSuccess: () => {
      toast.success('Worker updated')
      queryClient.invalidateQueries({ queryKey: ['ops', 'workers', jobId] })
    },
  })

  const chunkActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'retry' | 'skip' }) =>
      action === 'retry' ? operationsApi.retryChunk(id) : operationsApi.skipChunk(id),
    onSuccess: () => {
      toast.success('Chunk updated')
      queryClient.invalidateQueries({ queryKey: ['ops', 'chunks', jobId] })
    },
  })

  if (jobLoading || !job) return <FullPageSpinner />

  const stats = liveStats
  const status = stats?.status ?? job.status
  const progress = stats?.progress_pct ?? job.progress_pct

  const workerColumns: ColumnDef<Worker>[] = [
    { header: 'Worker', accessorKey: 'worker_id', cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'Host', accessorKey: 'host' },
    { header: 'Status', accessorKey: 'status', cell: ({ getValue }) => <WorkerStatusBadge status={getValue<Worker['status']>()} /> },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) =>
        canOperate ? (
          <div className="flex justify-end gap-1">
            {row.original.status === 'BUSY' && (
              <Button variant="ghost" size="sm" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'pause' })}>
                <Pause className="h-3.5 w-3.5" />
              </Button>
            )}
            {row.original.status === 'PAUSED' && (
              <Button variant="ghost" size="sm" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'resume' })}>
                <Play className="h-3.5 w-3.5" />
              </Button>
            )}
            {row.original.status !== 'QUARANTINED' && (
              <Button variant="ghost" size="sm" onClick={() => workerActionMutation.mutate({ id: row.original.worker_id, action: 'quarantine' })}>
                <ShieldAlert className="h-3.5 w-3.5 text-error" />
              </Button>
            )}
          </div>
        ) : null,
    },
  ]

  const chunkColumns: ColumnDef<any>[] = [
    { header: 'Chunk', accessorKey: 'id', cell: ({ getValue }) => <span className="mono text-small">{String(getValue()).slice(0, 8)}</span> },
    { header: 'Table', accessorKey: 'table_name' },
    { header: 'Status', accessorKey: 'status', cell: ({ getValue }) => <Badge tone={getValue() === 'failed' ? 'error' : getValue() === 'completed' ? 'success' : 'neutral'}>{String(getValue())}</Badge> },
    { header: 'Rows', accessorKey: 'row_count', cell: ({ getValue }) => formatNumber(getValue<number>()) },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) =>
        canOperate && row.original.status === 'failed' ? (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => chunkActionMutation.mutate({ id: row.original.id, action: 'retry' })}>
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => chunkActionMutation.mutate({ id: row.original.id, action: 'skip' })}>
              <SkipForward className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : null,
    },
  ]

  return (
    <div>
      <button onClick={() => navigate('/app/jobs')} className="mb-3 flex items-center gap-1 text-small text-text-secondary hover:text-text-primary">
        <ArrowLeft className="h-3.5 w-3.5" /> Back to jobs
      </button>

      <PageHeader
        title={job.name}
        description={
          <span className="flex items-center gap-2">
            <EngineIcon engine={job.source_engine} size="sm" /> → <EngineIcon engine={job.target_engine} size="sm" />
          </span>
        }
        actions={
          canOperate && (
            <div className="flex gap-2">
              {status === 'running' && (
                <Button variant="secondary" leftIcon={<Pause className="h-4 w-4" />} onClick={() => setConfirmAction('pause')}>
                  Pause
                </Button>
              )}
              {status === 'paused' && (
                <Button variant="secondary" leftIcon={<Play className="h-4 w-4" />} onClick={() => setConfirmAction('resume')}>
                  Resume
                </Button>
              )}
              {(status === 'running' || status === 'paused') && (
                <Button variant="danger" leftIcon={<XCircle className="h-4 w-4" />} onClick={() => setConfirmAction('cancel')}>
                  Cancel
                </Button>
              )}
            </div>
          )
        }
      />

      <div className="mb-6 flex items-center gap-3">
        <JobStatusBadge status={status} />
        <div className="flex-1">
          <ProgressBar value={progress} showLabel tone={status === 'failed' ? 'error' : 'action'} />
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile icon={Database} label="Rows migrated" value={formatNumber(stats?.rows_migrated ?? job.rows_migrated)} />
        <StatTile icon={Activity} label="Rows / sec" value={formatNumber(stats?.rows_per_sec)} />
        <StatTile icon={Users2} label="Active workers" value={String(stats?.active_workers ?? 0)} />
        <StatTile icon={Timer} label="ETA" value={stats?.eta_str ?? formatDuration(stats?.eta_seconds)} />
      </div>

      <Tabs
        tabs={[
          { key: 'overview', label: 'Overview' },
          { key: 'workers', label: 'Workers', count: workers.length },
          { key: 'chunks', label: 'Chunks', count: stats?.total_chunks },
          { key: 'drift', label: 'Drift & Tuning', count: driftEvents.length },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-5">
        {tab === 'overview' && stats && (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            {[
              { label: 'Completed', value: stats.completed_chunks, tone: 'success' as const },
              { label: 'Running', value: stats.running_chunks, tone: 'info' as const },
              { label: 'Pending', value: stats.pending_chunks, tone: 'neutral' as const },
              { label: 'Failed', value: stats.failed_chunks, tone: 'error' as const },
              { label: 'Skipped', value: stats.skipped_chunks, tone: 'warning' as const },
            ].map((c) => (
              <Card key={c.label} padding="sm">
                <p className="text-tiny text-text-secondary">{c.label}</p>
                <p className="mt-1 text-h3 text-text-primary">{formatNumber(c.value)}</p>
              </Card>
            ))}
          </div>
        )}

        {tab === 'workers' && <DataTable columns={workerColumns} data={workers} emptyMessage="No workers assigned yet" />}

        {tab === 'chunks' && <DataTable columns={chunkColumns} data={chunks} emptyMessage="No chunks yet" />}

        {tab === 'drift' && (
          <DataTable
            columns={[
              { header: 'Table', accessorKey: 'table_name' },
              { header: 'Type', accessorKey: 'drift_type' },
              { header: 'Severity', accessorKey: 'severity', cell: ({ getValue }) => <Badge tone={getValue() === 'critical' ? 'error' : 'warning'}>{String(getValue())}</Badge> },
              { header: 'Action taken', accessorKey: 'action_taken' },
            ] as ColumnDef<DriftEvent>[]}
            data={driftEvents}
            emptyMessage="No schema drift detected"
          />
        )}
      </div>

      <ConfirmDialog
        isOpen={!!confirmAction}
        onClose={() => setConfirmAction(null)}
        onConfirm={() => confirmAction && controlMutation.mutate(confirmAction)}
        title={`${confirmAction === 'pause' ? 'Pause' : confirmAction === 'resume' ? 'Resume' : 'Cancel'} this job?`}
        description={
          confirmAction === 'cancel'
            ? 'This will stop the migration immediately. Completed chunks are kept; in-flight chunks are rolled back.'
            : 'Workers will finish their current chunk before pausing.'
        }
        confirmLabel={confirmAction === 'cancel' ? 'Cancel job' : 'Confirm'}
        isDanger={confirmAction === 'cancel'}
        isLoading={controlMutation.isPending}
      />
    </div>
  )
}
