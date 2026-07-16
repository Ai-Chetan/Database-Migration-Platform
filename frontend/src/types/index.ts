// ── Enums ──────────────────────────────────────────────────────────────────

export type Role =
  | 'platform_admin'
  | 'tenant_admin'
  | 'migration_admin'
  | 'migration_operator'
  | 'read_only'
  | 'auditor'
  | 'api_client'

export type JobStatus = 'planning' | 'running' | 'completed' | 'failed' | 'paused' | 'cancelled'

export type Engine =
  | 'mysql'
  | 'postgresql'
  | 'sqlite'
  | 's3'
  | 'azure'
  | 'gcs'
  | 'kafka'
  | 'rest_api'
  | 'file'

export type Complexity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'

export type WorkerStatus = 'BUSY' | 'IDLE' | 'PAUSED' | 'QUARANTINED' | 'STOPPING' | 'OFFLINE'

export type ConnectionStatus = 'healthy' | 'failed' | 'untested'

export type MappingKind =
  | 'direct'
  | 'rename'
  | 'constant'
  | 'expression'
  | 'transform'
  | 'lookup'
  | 'mask'
  | 'synthesize'

export type ConversionSafety = 'safe' | 'lossy' | 'unsafe' | 'custom'

export type MaskingStrategy =
  | 'hash'
  | 'redact'
  | 'partial'
  | 'encrypt'
  | 'nullify'
  | 'fixed_value'
  | 'format_preserve'
  | 'synthesize'

export type ReportType =
  | 'migration_summary'
  | 'validation_report'
  | 'performance_report'
  | 'audit_report'
  | 'data_quality_report'
  | 'compliance_report'

export type ScheduleJobType = 'intelligence_scan' | 'data_quality_scan' | 'benchmark' | 'report' | 'migration'

// ── Core entities ────────────────────────────────────────────────────────────

export interface User {
  id: string
  email: string
  name: string
  role: Role
  tenant_id: string
  permissions: string[]
  is_active?: boolean
  last_login?: string | null
  created_at?: string
}

export interface Connection {
  id: string
  name: string
  engine: Engine
  host: string
  port: number
  database: string
  status: ConnectionStatus
  last_tested_at: string | null
  latency_ms: number | null
}

export interface Job {
  id: string
  name: string
  status: JobStatus
  source_engine: Engine
  target_engine: Engine
  source_connection_id: string
  target_connection_id: string
  mapping_project_id: string
  worker_count: number
  progress_pct: number
  rows_migrated: number
  started_at: string | null
  completed_at: string | null
  error_message: string | null
}

export interface LiveStats {
  status: JobStatus
  progress_pct: number
  total_chunks: number
  completed_chunks: number
  failed_chunks: number
  running_chunks: number
  pending_chunks: number
  skipped_chunks: number
  rows_migrated: number
  rows_per_sec: number
  active_workers: number
  avg_chunk_ms: number
  eta_seconds: number | null
  eta_str: string
  error_rate_pct: number
}

export interface Worker {
  worker_id: string
  status: WorkerStatus
  current_job_id: string | null
  last_heartbeat: string
  host: string
  pid: number
  pending_command: string | null
  is_quarantined: boolean
}

export interface Assessment {
  complexity: Complexity
  risk_level: string
  total_tables: number
  total_rows: number
  total_size_gb: number
  estimated_duration: string
  recommended_workers: number
  blocking_issues: string[]
  warnings: string[]
  recommendations: string[]
}

export interface SweepPoint {
  worker_count: number
  estimated_duration_str: string
  estimated_duration_sec: number
  estimated_cpu_source_pct: number
  estimated_cpu_target_pct: number
  failure_probability_pct: number
  bottleneck: string
}

export interface SimulationSweepResult {
  sweet_spot_workers: number
  sweet_spot_reason: string
  sweep: SweepPoint[]
}

export interface KnowledgeEntry {
  id: string
  entry_type: string
  source_engine: Engine
  target_engine: Engine
  title: string
  content: Record<string, any>
  tags: string[]
  usefulness_score: number
  reference_count: number
  created_at: string
}

export interface MaskingRule {
  id: string
  table_name: string
  column_name: string
  strategy: MaskingStrategy
  strategy_config: Record<string, any>
  is_active: boolean
}

export interface ScheduledJob {
  id: string
  name: string
  job_type: ScheduleJobType
  cron_expression: string
  timezone: string
  is_active: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_status: string | null
  run_count: number
}

export interface DriftEvent {
  id: string
  table_name: string
  drift_type: string
  column_name: string | null
  severity: 'critical' | 'warning'
  action_taken: string
  detected_at: string
  resolved_at: string | null
}

export interface TuningAction {
  action_type: string
  before_value: Record<string, any>
  after_value: Record<string, any>
  reason: string
  triggered_by: string
  created_at: string
}

export interface MappingTable {
  id: string
  source_table: string
  target_table: string
  status: string
  column_count: number
  mapped_count: number
}

export interface MappingColumn {
  id: string
  source_column: string
  source_type: string
  target_column: string
  target_type: string
  mapping_kind: MappingKind
  mapping_config: Record<string, any>
  conversion_safety: ConversionSafety
  is_active: boolean
}

export interface Project {
  id: string
  name: string
  source_engine: Engine
  target_engine: Engine
  status: string
  created_at: string
}

export interface ApiResult<T> {
  data: T
}
