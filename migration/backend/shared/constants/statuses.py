class MigrationJobStatus:
    """
    Lowercase to match the rest of the platform: Operations Console
    (operations/job_control.py), Reporting (reporting/generators/), and
    Approvals (enterprise/routers/approvals.py) all query/set job status
    using lowercase values ('pending', 'planning', 'running', 'paused',
    'completed', 'failed', 'cancelled', 'awaiting_approval'). The values
    below were previously UPPERCASE, which would have caused every job
    created via JobManager to silently never match those other modules'
    status filters (e.g. Operations Console's "list running jobs" query
    checks status='running', never 'RUNNING').
    """
    PENDING            = "pending"
    PLANNING           = "planning"
    QUEUED             = "queued"
    RUNNING            = "running"
    PAUSED             = "paused"
    AWAITING_APPROVAL  = "awaiting_approval"
    FAILED             = "failed"
    COMPLETED          = "completed"
    CANCELLED          = "cancelled"

class ChunkStatus:
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    FAILED = "failed"
    RETRYING = "retrying"
    COMPLETED = "completed"
    SKIPPED = "skipped"

class WorkerStatus:
    """Worker status IS uppercase by existing platform convention —
    see operations/worker_control/worker_control.py which sets
    'BUSY', 'IDLE', 'PAUSED', 'QUARANTINED', 'STOPPING'. Left unchanged."""
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    IDLE = "IDLE"
    OFFLINE = "OFFLINE"
    UNHEALTHY = "UNHEALTHY"
