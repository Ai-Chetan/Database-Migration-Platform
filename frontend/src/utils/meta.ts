import { Engine, JobStatus, WorkerStatus, Complexity } from '@/types'

export const ENGINE_LABELS: Record<Engine, string> = {
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  sqlite: 'SQLite',
  s3: 'Amazon S3',
  azure: 'Azure Blob',
  gcs: 'Google Cloud Storage',
  kafka: 'Kafka',
  rest_api: 'REST API',
  file: 'File',
}

export const JOB_STATUS_META: Record<JobStatus, { label: string; color: string; dot: string }> = {
  planning: { label: 'Planning', color: 'text-text-secondary bg-surface', dot: 'bg-text-tertiary' },
  running: { label: 'Running', color: 'text-info bg-sky-50', dot: 'bg-info' },
  completed: { label: 'Completed', color: 'text-success bg-green-50', dot: 'bg-success' },
  failed: { label: 'Failed', color: 'text-error bg-red-50', dot: 'bg-error' },
  paused: { label: 'Paused', color: 'text-warning bg-amber-50', dot: 'bg-warning' },
  cancelled: { label: 'Cancelled', color: 'text-text-tertiary bg-surface', dot: 'bg-text-tertiary' },
}

export const WORKER_STATUS_META: Record<WorkerStatus, { label: string; color: string }> = {
  BUSY: { label: 'Busy', color: 'text-info bg-sky-50' },
  IDLE: { label: 'Idle', color: 'text-text-secondary bg-surface' },
  PAUSED: { label: 'Paused', color: 'text-warning bg-amber-50' },
  QUARANTINED: { label: 'Quarantined', color: 'text-error bg-red-50' },
  STOPPING: { label: 'Stopping', color: 'text-warning bg-amber-50' },
  OFFLINE: { label: 'Offline', color: 'text-text-tertiary bg-surface' },
}

export const COMPLEXITY_META: Record<Complexity, { label: string; color: string }> = {
  LOW: { label: 'Low', color: 'text-success bg-green-50' },
  MEDIUM: { label: 'Medium', color: 'text-warning bg-amber-50' },
  HIGH: { label: 'High', color: 'text-error bg-red-50' },
  CRITICAL: { label: 'Critical', color: 'text-red-50 bg-error' },
}
