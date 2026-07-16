import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ColumnDef } from '@tanstack/react-table'
import { PlusCircle, CalendarClock, Play, Pencil, Trash2 } from 'lucide-react'
import { schedulerApi } from '@/api/scheduler'
import { ScheduledJob } from '@/types'
import { PageHeader, Button, DataTable, Badge, EmptyState, ConfirmDialog } from '@/components/common'
import { useDisclosure } from '@/hooks/useDisclosure'
import { usePermission } from '@/hooks/usePermission'
import { formatDateTime } from '@/utils/format'
import { ScheduledJobDrawer } from '@/components/features/scheduler/ScheduledJobDrawer'

export default function Scheduler() {
  const queryClient = useQueryClient()
  const canWrite = usePermission('scheduler:*')
  const drawer = useDisclosure()
  const [editingJob, setEditingJob] = useState<ScheduledJob | null>(null)
  const [deletingJob, setDeletingJob] = useState<ScheduledJob | null>(null)

  const { data: jobs = [], isLoading } = useQuery({ queryKey: ['scheduler', 'jobs'], queryFn: schedulerApi.list })

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => schedulerApi.toggle(id, is_active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scheduler', 'jobs'] }),
  })

  const runNowMutation = useMutation({
    mutationFn: (id: string) => schedulerApi.runNow(id),
    onSuccess: () => {
      toast.success('Job triggered')
      queryClient.invalidateQueries({ queryKey: ['scheduler', 'jobs'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => schedulerApi.remove(id),
    onSuccess: () => {
      toast.success('Schedule deleted')
      queryClient.invalidateQueries({ queryKey: ['scheduler', 'jobs'] })
      setDeletingJob(null)
    },
  })

  const columns: ColumnDef<ScheduledJob>[] = [
    { header: 'Name', accessorKey: 'name' },
    { header: 'Type', accessorKey: 'job_type', cell: ({ getValue }) => <Badge tone="neutral">{String(getValue()).replace('_', ' ')}</Badge> },
    { header: 'Schedule', accessorKey: 'cron_expression', cell: ({ getValue }) => <span className="mono text-small">{getValue<string>()}</span> },
    { header: 'Next run', accessorKey: 'next_run_at', cell: ({ getValue }) => <span className="text-small">{formatDateTime(getValue<string | null>())}</span> },
    { header: 'Last status', accessorKey: 'last_status', cell: ({ getValue }) => getValue() ? <Badge tone={getValue() === 'success' ? 'success' : 'error'}>{String(getValue())}</Badge> : <span className="text-text-tertiary">—</span> },
    {
      header: 'Active',
      accessorKey: 'is_active',
      cell: ({ row }) => (
        <button
          disabled={!canWrite}
          onClick={() => toggleMutation.mutate({ id: row.original.id, is_active: !row.original.is_active })}
          className={`h-5 w-9 rounded-full transition-colors ${row.original.is_active ? 'bg-action' : 'bg-border'}`}
        >
          <span className={`block h-4 w-4 rounded-full bg-white transition-transform ${row.original.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
        </button>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={() => runNowMutation.mutate(row.original.id)}><Play className="h-3.5 w-3.5" /></Button>
          {canWrite && (
            <>
              <Button variant="ghost" size="sm" onClick={() => { setEditingJob(row.original); drawer.open() }}><Pencil className="h-3.5 w-3.5" /></Button>
              <Button variant="ghost" size="sm" onClick={() => setDeletingJob(row.original)}><Trash2 className="h-3.5 w-3.5 text-error" /></Button>
            </>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Scheduler"
        description="Automate recurring intelligence scans, benchmarks, and reports."
        actions={canWrite && (
          <Button leftIcon={<PlusCircle className="h-4 w-4" />} onClick={() => { setEditingJob(null); drawer.open() }}>
            New scheduled job
          </Button>
        )}
      />

      {!isLoading && jobs.length === 0 ? (
        <EmptyState icon={CalendarClock} title="No scheduled jobs" description="Automate recurring scans and reports so you don't have to run them manually." actionLabel={canWrite ? 'New scheduled job' : undefined} onAction={canWrite ? drawer.open : undefined} />
      ) : (
        <DataTable columns={columns} data={jobs} isLoading={isLoading} />
      )}

      <ScheduledJobDrawer isOpen={drawer.isOpen} onClose={() => { drawer.close(); setEditingJob(null) }} job={editingJob} />

      <ConfirmDialog
        isOpen={!!deletingJob}
        onClose={() => setDeletingJob(null)}
        onConfirm={() => deletingJob && deleteMutation.mutate(deletingJob.id)}
        title={`Delete "${deletingJob?.name}"?`}
        description="This scheduled job will stop running immediately."
        confirmLabel="Delete"
        isLoading={deleteMutation.isPending}
      />
    </div>
  )
}
