# Worker Service - Complete Build Guide

## What You're Building

The Worker Service is the execution engine of your migration platform.
It consumes chunk messages from Redis and physically moves data from
the source database to the target database.

After this step, your pipeline will be fully functional end-to-end.

---

## Final File Structure

```
migration/
└── backend/
    └── worker_service/
        ├── main.py                          ← Entry point (start the worker here)
        └── app/
            ├── worker.py                    ← Main polling loop
            ├── executor/
            │   ├── __init__.py
            │   ├── chunk_executor.py        ← MOST IMPORTANT FILE
            │   ├── retry_executor.py        ← Retry decision logic
            │   └── transaction_manager.py   ← DB transaction wrapper
            ├── readers/
            │   ├── __init__.py
            │   ├── mysql_reader.py          ← Stream from MySQL source
            │   └── postgres_reader.py       ← Stream from PostgreSQL source
            ├── writers/
            │   ├── __init__.py
            │   ├── mysql_writer.py          ← Bulk insert to MySQL target
            │   └── postgres_writer.py       ← Bulk insert to PostgreSQL target
            └── monitoring/
                ├── __init__.py
                └── heartbeat.py             ← Worker health tracking

test_worker_e2e.py                           ← End-to-end test
```

---

## Step 1 — Create the directory structure

Run from your `migration/` directory:

```powershell
New-Item -ItemType Directory -Force -Path backend/worker_service/app/executor
New-Item -ItemType Directory -Force -Path backend/worker_service/app/readers
New-Item -ItemType Directory -Force -Path backend/worker_service/app/writers
New-Item -ItemType Directory -Force -Path backend/worker_service/app/monitoring
```

Then create blank `__init__.py` files in each directory:

```powershell
"" | Out-File -FilePath backend/worker_service/__init__.py
"" | Out-File -FilePath backend/worker_service/app/__init__.py
"" | Out-File -FilePath backend/worker_service/app/executor/__init__.py
"" | Out-File -FilePath backend/worker_service/app/readers/__init__.py
"" | Out-File -FilePath backend/worker_service/app/writers/__init__.py
"" | Out-File -FilePath backend/worker_service/app/monitoring/__init__.py
```

---

## Step 2 — Copy files from this guide

Copy each file from this guide to the correct location:

| File in this guide    | Destination in your project                                         |
|-----------------------|---------------------------------------------------------------------|
| main.py               | migration/backend/worker_service/main.py                            |
| worker.py             | migration/backend/worker_service/app/worker.py                      |
| chunk_executor.py     | migration/backend/worker_service/app/executor/chunk_executor.py     |
| retry_executor.py     | migration/backend/worker_service/app/executor/retry_executor.py     |
| transaction_manager.py| migration/backend/worker_service/app/executor/transaction_manager.py|
| mysql_reader.py       | migration/backend/worker_service/app/readers/mysql_reader.py        |
| postgres_reader.py    | migration/backend/worker_service/app/readers/postgres_reader.py     |
| mysql_writer.py       | migration/backend/worker_service/app/writers/mysql_writer.py        |
| postgres_writer.py    | migration/backend/worker_service/app/writers/postgres_writer.py     |
| heartbeat.py          | migration/backend/worker_service/app/monitoring/heartbeat.py        |
| test_worker_e2e.py    | migration/test_worker_e2e.py                                        |

---

## Step 3 — Install new dependencies

```powershell
pip install mysql-connector-python psutil
```

---

## Step 4 — Verify your source_config and target_config

Your `create_job.py` currently creates jobs with:

```python
source_config={"engine": "mysql"}
target_config={"engine": "postgres"}
```

The worker needs connection details to actually connect to databases.
Update `create_job.py` to include:

```python
source_config={
    "engine": "mysql",
    "host": "localhost",
    "port": 3306,
    "database": "your_source_db",
    "user": "root",
    "password": "your_password"
}

target_config={
    "engine": "mysql",       # or "postgresql"
    "host": "localhost",
    "port": 3306,
    "database": "your_target_db",
    "user": "root",
    "password": "your_password"
}
```

---

## Step 5 — Prepare your test databases

Create a source table with test data:

```sql
-- In your SOURCE database:
CREATE TABLE users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    created_at DATETIME DEFAULT NOW()
);

-- Insert 50,000 test rows (or however many you want):
INSERT INTO users (name, email)
SELECT
    CONCAT('User ', seq.n),
    CONCAT('user', seq.n, '@example.com')
FROM (
    SELECT a.N + b.N * 1000 + 1 AS n
    FROM
        (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION
         SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) a,
        (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION
         SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9) b,
        (SELECT 0 AS N UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) c
) seq
LIMIT 50000;
```

Create a matching empty table in your TARGET database:

```sql
-- In your TARGET database:
CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    created_at DATETIME
);
```

---

## Step 6 — Run the end-to-end test

```powershell
# From the migration/ directory:
python test_worker_e2e.py
```

Expected output:
```
============================================================
WORKER END-TO-END TEST
============================================================

[1] Setting up metadata DB schema...
    ✓ Schema ready

[2] Creating migration job...
    ✓ Job created: abc-123...

[3] Creating table metadata...
    ✓ Table entry created: def-456...

[4] Generating chunks (total_rows=50,000, chunk_size=10,000)...
    ✓ Generated 5 chunks
    ✓ Redis queue depth: 5

[5] Running worker to process 5 chunks...
    ✓ Chunk 1/5 completed
    ✓ Chunk 2/5 completed
    ✓ Chunk 3/5 completed
    ✓ Chunk 4/5 completed
    ✓ Chunk 5/5 completed

    Processed: 5
    Failed:    0

[6] Verifying final state in metadata DB...
    Completed chunks in DB: 5/5
    Final job status: completed
    Job completed_chunks: 5

✅ END-TO-END TEST PASSED
============================================================
```

---

## Step 7 — Run as a continuous worker

Once the test passes, run the worker as a long-running process:

```powershell
# From the migration/ directory:
python backend/worker_service/main.py
```

Now when you push chunks to Redis (via create_job.py or any other means),
the worker will automatically pick them up and process them.

---

## How the Data Flows

```
create_job.py
    ↓ Creates Job in PostgreSQL
    ↓ Creates Table entry in PostgreSQL
    ↓ Planner generates Chunks in PostgreSQL
    ↓ Pushes chunk messages to Redis

Worker (main.py → worker.py)
    ↓ BRPOP from Redis (blocks waiting for messages)
    ↓ Gets: {job_id, table_id, chunk_id}

ChunkExecutor (chunk_executor.py)
    ↓ Fetches chunk from PostgreSQL (get pk_start, pk_end)
    ↓ Marks chunk status = "running" in PostgreSQL

MySQLReader / PostgresReader
    ↓ SELECT * FROM users WHERE id BETWEEN pk_start AND pk_end
    ↓ Yields rows in batches of 5000 (memory safe)

MySQLWriter / PostgresWriter
    ↓ INSERT INTO users (...) VALUES (batch of 5000 rows)
    ↓ COMMIT

ChunkExecutor
    ↓ Validates: source_count == rows_written
    ↓ Marks chunk status = "completed" in PostgreSQL
    ↓ Updates job.completed_chunks counter
    ↓ If all chunks done → marks job status = "completed"
```

---

## Common Issues and Fixes

**Issue: "Module not found" errors**
Fix: Make sure you're running from the `migration/` directory, not from `migration/backend/`.

**Issue: MySQL connection refused**
Fix: Check your MySQL is running and credentials in source_config are correct.

**Issue: Chunk status stays "running" forever**
Fix: Worker crashed during execution. Reset stale chunks:
```sql
UPDATE migration_chunks
SET status = 'pending', worker_id = NULL
WHERE status = 'running'
AND last_heartbeat < NOW() - INTERVAL '5 minutes';
```

**Issue: Row count mismatch validation fails**
Fix: Usually means some rows were inserted but not all. Check target DB for partial inserts.

**Issue: psutil not found**
Fix: `pip install psutil`

**Issue: mysql-connector-python not found**
Fix: `pip install mysql-connector-python`

---

## What's Next After This

Once the worker is running successfully:

1. **Resume Capability** — Add a startup scan that picks up any "running" chunks from crashed workers
2. **Retry Queue Consumer** — A second loop that reads from RETRY_QUEUE with backoff delays
3. **Multi-worker** — Run multiple `main.py` processes simultaneously (they automatically share work via Redis)
4. **Monitoring API** — FastAPI endpoints to query job status, chunk progress
5. **React Frontend** — Dashboard showing real-time migration progress

The hardest part is now done. The core data pipeline works.
