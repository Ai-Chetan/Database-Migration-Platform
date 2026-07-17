import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Workflow,
  Plug,
  Database,
  Users2,
  PlusCircle,
  ArrowRight,
  AlertTriangle,
} from 'lucide-react'
import { jobsApi } from '@/api/jobs'
import { connectionsApi } from '@/api/connections'
import { operationsApi } from '@/api/operations'
import {
  Card,
  CardHeader,
  CardTitle,
  Button,
  PageHeader,
  JobStatusBadge,
  EngineIcon,
  ProgressBar,
  EmptyState,
  SkeletonCard,
} from '@/components/common'
import { formatNumber, formatRelativeTime } from '@/utils/format'
import { useAuthStore } from '@/store/auth'
import { useLiveThroughput } from '@/hooks/useLiveThroughput'
import { ThroughputChart } from '@/components/features/dashboard/ThroughputChart'

function KpiCard({
  icon: Icon,
  label,
  value,
  isLoading,
  accent,
  live,
}: {
  icon: React.ElementType
  label: string
  value: string
  isLoading?: boolean
  accent?: string
  live?: boolean
}) {
  if (isLoading) return <SkeletonCard />
  return (
    <Card hoverable>
      <div className="flex items-center justify-between">
        <div>
          <p className="flex items-center gap-1.5 text-small text-text-secondary">
            {label}
            {live && <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-dot" />}
          </p>
          <p className="mt-1 text-h2 tabular-nums text-text-primary">{value}</p>
        </div>
        <div className={`flex h-10 w-10 items-center justify-center rounded ${accent || 'bg-action/10'}`}>
          <Icon className="h-5 w-5 text-action" />
        </div>
      </div>
    </Card>
  )
}

export default function Dashboard() {
  const user = useAuthStore((s) => s.user)

  const jobsQuery = useQuery({ queryKey: ['jobs', 'list'], queryFn: () => jobsApi.list({ limit: 8 }) })
  const connectionsQuery = useQuery({ queryKey: ['connections', 'list'], queryFn: connectionsApi.list })
  const workersQuery = useQuery({ queryKey: ['ops', 'workers'], queryFn: () => operationsApi.listWorkers() })

  const jobs = jobsQuery.data ?? []
  const connections = connectionsQuery.data ?? []
  const workers = workersQuery.data ?? []

  const activeJobs = jobs.filter((j) => j.status === 'running').length
  const runningJobs = jobs.filter((j) => j.status === 'running')
  const totalRows = jobs.reduce((sum, j) => sum + (j.rows_migrated || 0), 0)
  const healthyConnections = connections.filter((c) => c.status === 'healthy').length
  const activeWorkers = workers.filter((w) => w.status === 'BUSY').length

  const throughputSeries = useLiveThroughput(runningJobs)

  const isLoading = jobsQuery.isLoading || connectionsQuery.isLoading || workersQuery.isLoading

  return (
    <div>
      <PageHeader
        title={`Welcome back${user?.name ? `, ${user.name.split(' ')[0]}` : ''}`}
        description="Here's what's happening across your migrations right now."
        actions={
          <Link to="/app/jobs/new">
            <Button leftIcon={<PlusCircle className="h-4 w-4" />}>New Migration</Button>
          </Link>
        }
      />

      {/* KPI row */}
      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          icon={Workflow}
          label="Active jobs"
          value={formatNumber(activeJobs)}
          isLoading={isLoading}
          live={activeJobs > 0}
        />
        <KpiCard icon={Database} label="Rows migrated" value={formatNumber(totalRows)} isLoading={isLoading} />
        <KpiCard
          icon={Plug}
          label="Healthy connections"
          value={`${healthyConnections}/${connections.length}`}
          isLoading={isLoading}
        />
        <KpiCard icon={Users2} label="Active workers" value={formatNumber(activeWorkers)} isLoading={isLoading} />
      </div>

      <div className="mb-6">
        <ThroughputChart series={throughputSeries} activeJobCount={activeJobs} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Recent jobs */}
        <div className="lg:col-span-2">
          <Card padding="none">
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
              <CardTitle>Recent migration jobs</CardTitle>
              <Link to="/app/jobs" className="flex items-center gap-1 text-small text-action hover:underline">
                View all <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            {jobsQuery.isLoading ? (
              <div className="p-6">
                <SkeletonCard />
              </div>
            ) : jobs.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={Workflow}
                  title="No migration jobs yet"
                  description="Kick off your first migration to see it tracked here."
                  actionLabel="New Migration"
                  onAction={() => (window.location.href = '/app/jobs/new')}
                />
              </div>
            ) : (
              <div className="divide-y divide-border">
                {jobs.map((job) => (
                  <Link
                    key={job.id}
                    to={`/app/jobs/${job.id}`}
                    className="flex items-center gap-4 px-6 py-4 hover:bg-surface"
                  >
                    <EngineIcon engine={job.source_engine} size="sm" />
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
                    <EngineIcon engine={job.target_engine} size="sm" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-small font-medium text-text-primary">{job.name}</p>
                      <p className="text-tiny text-text-tertiary">
                        {job.started_at ? `Started ${formatRelativeTime(job.started_at)}` : 'Not started'}
                      </p>
                    </div>
                    <div className="w-28 shrink-0">
                      <ProgressBar value={job.progress_pct} size="sm" />
                    </div>
                    <JobStatusBadge status={job.status} />
                  </Link>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* Connection health */}
        <div>
          <Card padding="none">
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
              <CardTitle>Connection health</CardTitle>
              <Link to="/app/connections" className="text-small text-action hover:underline">
                Manage
              </Link>
            </div>
            {connectionsQuery.isLoading ? (
              <div className="p-6">
                <SkeletonCard />
              </div>
            ) : connections.length === 0 ? (
              <div className="p-6">
                <EmptyState icon={Plug} title="No connections yet" description="Add a source or target connection to get started." />
              </div>
            ) : (
              <div className="divide-y divide-border">
                {connections.slice(0, 6).map((c) => (
                  <div key={c.id} className="flex items-center gap-3 px-6 py-3">
                    <EngineIcon engine={c.engine} size="sm" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-small font-medium text-text-primary">{c.name}</p>
                      <p className="truncate text-tiny text-text-tertiary">{c.host}</p>
                    </div>
                    {c.status === 'healthy' && (
                      <span className="h-2 w-2 shrink-0 rounded-full bg-success" title="Healthy" />
                    )}
                    {c.status === 'failed' && (
                      <AlertTriangle className="h-4 w-4 shrink-0 text-error" aria-label="Failed" />
                    )}
                    {c.status === 'untested' && (
                      <span className="h-2 w-2 shrink-0 rounded-full bg-text-tertiary" title="Untested" />
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
