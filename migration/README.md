# Migration Platform Kernel — Backend

Self-hosted enterprise database migration platform (FastAPI + PostgreSQL metadata DB + Redis queue + workers).

## Quick start with Docker (recommended)
```powershell
cd E:\Project\Migration\migration
copy .env.example .env          # then edit .env: set POSTGRES_PASSWORD, JWT_SECRET, MIGRATION_ENCRYPTION_KEY
docker compose up --build       # postgres, redis, mysql(3307), api(8000), worker
docker compose up --build --scale worker=3     # more workers
docker compose --profile frontend up           # also the React dev server on :5173 (set FRONTEND_DIR in .env)
```
The first start creates the 73-table metadata schema from `db/baseline_schema.sql` automatically.
To reset everything: `docker compose down -v`.

Smoke test (inside the stack): `docker compose exec api python tools/smoke_e2e.py --scenario all`

## Running without Docker (what you did before)
```powershell
# 1. Redis is REQUIRED:  docker run -d --name redis -p 6379:6379 redis:7
# 2. venv + deps
python -m venv venv ; .\venv\Scripts\activate
pip install -r requirements.txt
# 3. metadata DB (first time only)
psql -U postgres -c "CREATE DATABASE migration_metadata"
psql -U postgres -d migration_metadata -f db\baseline_schema.sql
#    existing database from before S02? apply the newer SQL instead:
psql -U postgres -d migration_metadata -f db_migrations\021_fix_status_contract_and_missing_columns.sql
# 4. API (terminal 1)
$env:STRICT_ROUTERS="true"      # optional: refuse to start if any router fails to import
uvicorn backend.main:app --port 8000 --reload
# 5. worker (terminal 2) - start several for parallelism, each with a different WORKER_ID
$env:WORKER_ID="w1" ; python -m backend.worker_service.app.worker
```

## Checks
| What | Command |
|---|---|
| Are all routers loaded? | open `http://localhost:8000/health/routers` (`"ok": true`, 39 modules) |
| Full migration smoke test | `python tools\smoke_e2e.py --scenario all` (set `MYSQL_USER`, `MYSQL_PASSWORD`, `SRC_DB`, `TGT_DB` if different from the defaults) |
| Frontend/backend/DB mismatch scan | `python tools\contract_audit.py --schema <dump.sql> --backend backend --frontend ..\frontend\src` |

## Environment variables (see `.env.example`)
`POSTGRES_*`, `REDIS_*`, `JWT_SECRET` (>=32 chars in production), `MIGRATION_ENCRYPTION_KEY` (keep stable),
`CORS_ORIGINS`, `STRICT_ROUTERS`, `SQL_ECHO`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `LOG_LEVEL`, `APP_ENV`.
Worker only: `WORKER_ID` (unique per process), `QUEUE_TIMEOUT` (seconds, default 5). The worker no longer needs `TENANT_ID`: each job carries its tenant.

## Security rules
Never commit or share `.env`. `.gitignore` already excludes `.env` and `.env.*`.
