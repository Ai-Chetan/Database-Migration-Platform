# Reliability Layer — Complete Guide

## What Was Built

5 reliability components that take your migration platform from
"works in a demo" to "safe for 5TB enterprise migrations".

---

## File Map — Where Everything Goes

```
migration/backend/worker_service/app/

├── resume_manager.py               ← Priority 1 (NEW)
├── stale_chunk_recovery.py         ← Priority 2 (NEW)

├── writers/
│   └── idempotent_writer.py        ← Priority 3 (NEW)

├── validation/
│   ├── __init__.py                 ← create this (empty)
│   └── checksum_validator.py       ← Priority 4 (NEW)

├── progress/
│   ├── __init__.py                 ← create this (empty)
│   └── job_progress_engine.py      ← Priority 5 (NEW)

├── executor/
│   └── chunk_executor.py           ← REPLACE with chunk_executor_updated.py

└── worker.py                       ← REPLACE with worker_updated.py
```

---

## Step 1 — Create New Directories

```powershell
# From migration/ directory:
New-Item -ItemType Directory -Force -Path backend/worker_service/app/validation
New-Item -ItemType Directory -Force -Path backend/worker_service/app/progress

"" | Out-File backend/worker_service/app/validation/__init__.py
"" | Out-File backend/worker_service/app/progress/__init__.py
```

---

## Step 2 — Copy Files

| File in this package                 | Destination                                                              |
|--------------------------------------|--------------------------------------------------------------------------|
| resume_manager.py                    | backend/worker_service/app/resume_manager.py                             |
| stale_chunk_recovery.py              | backend/worker_service/app/stale_chunk_recovery.py                       |
| idempotent_writer.py                 | backend/worker_service/app/writers/idempotent_writer.py                  |
| checksum_validator.py                | backend/worker_service/app/validation/checksum_validator.py              |
| job_progress_engine.py               | backend/worker_service/app/progress/job_progress_engine.py               |
| chunk_executor_updated.py            | backend/worker_service/app/executor/chunk_executor.py  ← REPLACE         |
| worker_updated.py                    | backend/worker_service/app/worker.py                   ← REPLACE         |

---

## Priority 1 — Resume Manager

### The Problem

Without resume, a crash after 12 hours of a 16-hour migration
means restarting from zero. That's unacceptable for enterprise customers.

### How It Works

On every worker startup, before entering the polling loop:

```
scan migration_chunks WHERE status IN ('pending', 'running', 'retrying')
    for each pending  → push to MIGRATION_QUEUE
    for each running (stale heartbeat) → reset to pending, push to queue
    for each retrying → push to RETRY_QUEUE
    completed chunks  → skip entirely, never re-run
```

Chunk status is the source of truth. The queue is just a pointer to what
needs to be done. If the queue is lost (Redis restart), the DB state lets
you rebuild it completely.

### Example

```
Before crash:
    Chunk 1  COMPLETED ✓
    Chunk 2  COMPLETED ✓
    Chunk 3  RUNNING   ← was being processed
    Chunk 4  PENDING
    Chunk 5  PENDING

After restart, ResumeManager runs:
    Chunk 1  skip (completed)
    Chunk 2  skip (completed)
    Chunk 3  reset to pending → push to queue
    Chunk 4  push to queue
    Chunk 5  push to queue

Result: 3 chunks requeued, migration continues from where it stopped.
```

---

## Priority 2 — Stale Chunk Recovery

### The Problem

Worker picks up chunk #47, marks it RUNNING.
Worker process is killed by OS (OOM, Docker stop, power loss).
Chunk #47 stays RUNNING forever. Job never completes.

### How It Works

A background thread runs every 60 seconds inside every worker process.

```sql
SELECT * FROM migration_chunks
WHERE status = 'running'
AND last_heartbeat < NOW() - INTERVAL '15 minutes'
```

For each stale chunk:
- `retry_count++`
- If under max_retries: reset to `pending`, push to queue
- If at max_retries: mark `failed` permanently

### Why 15 minutes?

The heartbeat updates every 10 seconds.
If a worker is alive, the heartbeat timestamp will never be 15 minutes old.
If the heartbeat is 15+ minutes old, the worker is definitely dead.

---

## Priority 3 — Idempotency

### The Problem

Without idempotency:
```
Chunk #47 executes → writes 100,000 rows → worker crashes
Stale recovery requeues chunk #47
Chunk #47 executes again → writes 100,000 more rows
Result: 200,000 rows in target, 100,000 are DUPLICATES
```

### The Solution

`IdempotentMySQLWriter` uses `INSERT IGNORE`:
```sql
INSERT IGNORE INTO users (id, name, email) VALUES (...)
```
If a row with the same primary key already exists: silently skip it.
Result: running the same chunk twice = same result as running it once.

`IdempotentPostgresWriter` uses `ON CONFLICT DO NOTHING`:
```sql
INSERT INTO users (id, name, email) VALUES (...) ON CONFLICT DO NOTHING
```
Same behavior for PostgreSQL.

### Strategy 2 (nuclear option)

For tables without primary key constraints, use `delete_and_reinsert()`:
```
DELETE FROM target WHERE pk BETWEEN pk_start AND pk_end
INSERT all source rows fresh
```
This guarantees the target exactly mirrors the source for this chunk,
regardless of what was there before.

---

## Priority 4 — Checksum Validation

### The Problem

Row count alone proves nothing about data correctness:
```
Source:  id=1 Alice,  id=2 Bob
Target:  id=1 Alice,  id=2 NULL

Count: 2 = 2  ✓  (passes count check)
But Bob's data is corrupted/missing!
```

### How It Works

After writing a chunk, compute MD5 of ALL column values in the chunk
on both source and target, then compare.

MySQL:
```sql
SELECT MD5(CAST(SUM(CONV(SUBSTRING(MD5(CONCAT(col1, '|', col2, ...)), 1, 8), 16, 10)) AS CHAR))
FROM users WHERE id BETWEEN 1 AND 100000
```

PostgreSQL:
```sql
SELECT MD5(CAST(BIT_XOR(('x'||SUBSTRING(MD5(col1||'|'||col2||...),1,8))::BIT(32)::BIGINT::BIT(64)) AS TEXT))
FROM users WHERE id BETWEEN 1 AND 100000
```

If source checksum ≠ target checksum → exception → chunk marked failed → requeued.

The checksum is stored in `migration_chunks.checksum` for audit purposes.

### What Gets Stored

```
migration_chunks:
    checksum            = "a3f9b2c1..."     (source checksum)
    validation_status   = "passed"
    source_row_count    = 100000
    target_row_count    = 100000
```

---

## Priority 5 — Job Progress Engine

### The Problem

16-hour migration. No progress visibility.
Customer calls: "Is it done? What's happening?"
You have no answer.

### How It Works

Read-only component. Queries the DB and computes:

```python
engine = JobProgressEngine()
progress = engine.get_progress(db, job_id="abc-123")
```

Returns:
```json
{
    "job_id": "abc-123",
    "status": "running",
    "completion_pct": 52.3,
    "rows_migrated": 3400000,
    "rows_total": 6500000,
    "rows_remaining": 3100000,
    "chunks_completed": 34,
    "chunks_total": 65,
    "chunks_failed": 2,
    "chunks_running": 4,
    "chunks_pending": 25,
    "throughput_rps": 45000,
    "throughput_human": "45,000 rows/sec",
    "elapsed_seconds": 75,
    "elapsed_human": "1 minute, 15s",
    "eta_seconds": 68,
    "eta_human": "1 minute, 8s",
    "started_at": "2026-06-21T10:30:00",
    "completed_at": null
}
```

### Throughput Calculation

Uses the last 10 completed chunks (not global average).
This gives a more accurate current throughput that adapts as conditions change.

```
throughput_rps = avg(rows_processed / (duration_ms / 1000)) over last 10 chunks
```

### ETA Calculation

```
eta_seconds = rows_remaining / throughput_rps
```

This requires `duration_ms` and `rows_processed` to be filled in on completed chunks.
The updated `chunk_executor.py` fills these in correctly.

### Per-Table Progress

```python
table_progress = engine.get_table_progress(db, job_id="abc-123")
```

Returns:
```json
[
    {"table_name": "users",   "completion_pct": 100.0, "status": "completed"},
    {"table_name": "orders",  "completion_pct": 45.2,  "status": "running"},
    {"table_name": "products","completion_pct": 0.0,   "status": "pending"}
]
```

---

## How They All Work Together

```
Worker starts
    │
    ├── ResumeManager.resume_incomplete_jobs()
    │       Scans DB for incomplete work
    │       Pushes PENDING/RUNNING(stale)/RETRYING chunks back to Redis
    │
    ├── StaleChunkRecovery.start()
    │       Background thread checks every 60s for dead workers
    │
    └── Polling loop starts
            │
            BRPOP Redis (blocks)
            │
            message arrives → ChunkExecutor.execute()
                    │
                    ├── IdempotentWriter (no duplicates on retry)
                    ├── ChecksumValidator (data integrity proven)
                    ├── metrics recorded (feeds JobProgressEngine)
                    └── chunk marked COMPLETED
```

---

## Testing Each Component

### Test Resume Manager
```python
from backend.worker_service.app.resume_manager import ResumeManager
from backend.shared.config.database import get_db

db = next(get_db())
rm = ResumeManager()

# Check what's in the DB before
summary = rm.get_job_resume_summary(db, job_id="your-job-id")
print(summary)
# {"completed": 3, "pending": 2, "running": 1, "total": 6}

# Run resume
rm.resume_incomplete_jobs(db)
# Requeues pending and stale running chunks
```

### Test Stale Chunk Recovery
```python
from backend.worker_service.app.stale_chunk_recovery import StaleChunkRecovery
from backend.shared.config.database import get_db

db = next(get_db())
recovery = StaleChunkRecovery()
result = recovery.run_once(db)
print(result)
# {"stale_chunks_found": 2, "recovered": 2, "permanently_failed": 0}
```

### Test Checksum Validator
```python
from backend.worker_service.app.validation.checksum_validator import ChecksumValidator

validator = ChecksumValidator()
result = validator.validate_chunk(
    source_config={"engine": "mysql", "host": "...", "database": "source_db", ...},
    target_config={"engine": "mysql", "host": "...", "database": "target_db", ...},
    table_name="users",
    pk_column="id",
    pk_start=1,
    pk_end=100000
)
print(result.passed)           # True
print(result.source_checksum)  # "a3f9b2c1..."
print(result.details)          # "Checksums match. Rows: 100000"
```

### Test Progress Engine
```python
from backend.worker_service.app.progress.job_progress_engine import JobProgressEngine
from backend.shared.config.database import get_db

db = next(get_db())
engine = JobProgressEngine()
progress = engine.get_progress(db, job_id="your-job-id")
print(f"{progress['completion_pct']}% complete")
print(f"ETA: {progress['eta_human']}")
print(f"Throughput: {progress['throughput_human']}")
```

---

## What's Next

With the Reliability Layer complete, your platform can handle enterprise migrations.

The next natural phases are:

1. **Monitoring API** — FastAPI endpoints that expose JobProgressEngine data
   over HTTP so a frontend can poll it in real time
2. **React Dashboard** — Progress bars, ETA, throughput charts, chunk status grid
3. **Retry Queue Consumer** — A second BRPOP loop that reads from RETRY_QUEUE
   with exponential backoff delays between retries
4. **Multi-worker Scaling** — Run 5, 10, 50 `main.py` processes simultaneously;
   they automatically share chunks via Redis with no coordination needed
