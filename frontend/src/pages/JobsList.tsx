import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ColumnDef } from '@tanstack/react-table'
import { PlusCircle, Workflow, ArrowRight } from 'lucide-react'
import { jobsApi } from '@/api/jobs'
import { Job, JobStatus } from '@/types'
import { PageHeader, Button, DataTable, EngineIcon, JobStatusBadge, ProgressBar, EmptyState, Tabs } from '@/components/common'
import { formatNumber, formatRelativeTime } from '@/utils/format'
import { usePermission } from '@/hooks/usePermission'

const FILTERS: { key: string; label: string; status?: JobStatus }[] = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running', status: 'running' },
  { key: 'completed', label: 'Completed', status: 'completed' },
  { key: 'failed', label: 'Failed', status: 'failed' },
  { key: 'paused', label: 'Paused', status: 'paused' },
]

export default function JobsList() {
  const navigate = useNavigate()
  const canStart = usePermission('jobs:start')
  const [filter, setFilter] = useState('all')

  const activeFilter = FILTERS.find((f) => f.key === filter)

  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ['jobs', 'list', activeFilter?.status],
    queryFn: () => jobsApi.list({ status: activeFilter?.status, limit: 100 }),
  })

  const counts = FILTERS.reduce<Record<string, number>>((acc, f) => {
    acc[f.key] = f.status ? jobs.filter((j) => j.status === f.status).length : jobs.length
    return acc
  }, {})

  const columns: ColumnDef<Job>[] = [
    {
      header: 'Job',
      accessorKey: 'name',
      cell: ({ row }) => (
        <div className="flex items-center gap-3">
          <EngineIcon engine={row.original.source_engine} size="sm" />
          <ArrowRight className="h-3 w-3 text-text-tertiary" />
          <EngineIcon engine={row.original.target_engine} size="sm" />
          <span className="font-medium text-text-primary">{row.original.name}</span>
        </div>
      ),
    },
    {
      header: 'Status',
      accessorKey: 'status',
      cell: ({ getValue }) => <JobStatusBadge status={getValue<JobStatus>()} />,
    },
    {
      header: 'Progress',
      accessorKey: 'progress_pct',
      cell: ({ row }) => (
        <div className="w-32">
          <ProgressBar value={row.original.progress_pct} size="sm" showLabel />
        </div>
      ),
    },
    {
      header: 'Rows migrated',
      accessorKey: 'rows_migrated',
      cell: ({ getValue }) => <span className="text-small">{formatNumber(getValue<number>())}</span>,
    },
    {
      header: 'Workers',
      accessorKey: 'worker_count',
    },
    {
      header: 'Started',
      accessorKey: 'started_at',
      cell: ({ getValue }) => (
        <span className="text-small text-text-secondary">{formatRelativeTime(getValue<string | null>())}</span>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Migration Jobs"
        description="All migration jobs across your connections."
        actions={
          canStart && (
            <Button leftIcon={<PlusCircle className="h-4 w-4" />} onClick={() => navigate('/app/jobs/new')}>
              New Migration
            </Button>
          )
        }
      />

      <div className="mb-4">
        <Tabs
          tabs={FILTERS.map((f) => ({ key: f.key, label: f.label, count: counts[f.key] }))}
          active={filter}
          onChange={setFilter}
        />
      </div>

      {!isLoading && jobs.length === 0 ? (
        <EmptyState
          icon={Workflow}
          title="No jobs found"
          description="No migration jobs match this filter yet."
          actionLabel={canStart ? 'New Migration' : undefined}
          onAction={canStart ? () => navigate('/app/jobs/new') : undefined}
        />
      ) : (
        <DataTable
          columns={columns}
          data={jobs}
          isLoading={isLoading}
          onRowClick={(job) => navigate(`/app/jobs/${job.id}`)}
        />
      )}
    </div>
  )
}
