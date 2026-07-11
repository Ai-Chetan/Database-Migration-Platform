"""
Operations Console — FastAPI Application
File: migration/backend/operations/main.py

Part 10: Operations Console Backend. Runs on port 8016.

The Kubernetes Dashboard equivalent for the Migration Platform.
Gives operators manual control over every aspect of a live migration
without needing to SSH into servers or write SQL.

Three control domains:
  Worker Control  → pause/resume/kill/quarantine/scale/drain workers
  Chunk Control   → reassign/retry/skip individual chunks
  Job Control     → pause/resume/cancel jobs, rerun validation, live stats
  Maintenance     → maintenance mode, emergency stop

All actions:
  - Write to Redis immediately (workers see changes on next chunk pull)
  - Update PostgreSQL state
  - Log to operations_actions table (immutable audit trail)
  - Publish to Event Bus (Notification plugins can alert on manual interventions)

Start:
    cd migration/
    uvicorn backend.operations.main:app --host 0.0.0.0 --port 8016 --reload

Docs: http://localhost:8016/docs

ALL ENDPOINTS:

── WORKER CONTROL ────────────────────────────────────────────────────────────
    GET  /ops/workers                          List all workers
    GET  /ops/workers/{job_id}/job             List workers for a job
    POST /ops/workers/{id}/pause               Pause after current chunk
    POST /ops/workers/{id}/resume              Resume pulling
    POST /ops/workers/{id}/kill                Stop immediately
    POST /ops/workers/{id}/quarantine          Pause + flag for investigation
    POST /ops/jobs/{id}/workers/scale          Set target worker count
    POST /ops/jobs/{id}/workers/drain          Drain all workers for a job

── CHUNK CONTROL ─────────────────────────────────────────────────────────────
    GET  /ops/jobs/{id}/chunks/problems        Chunks needing attention
    GET  /ops/chunks/{id}                      Full chunk detail
    POST /ops/chunks/{id}/reassign             Move to worker or queue
    POST /ops/chunks/{id}/retry                Force retry
    POST /ops/chunks/{id}/skip                 Skip (requires reason)

── JOB CONTROL ───────────────────────────────────────────────────────────────
    GET  /ops/jobs/{id}/live-stats             Real-time stats + ETA
    POST /ops/jobs/{id}/pause                  Pause gracefully
    POST /ops/jobs/{id}/resume                 Resume paused job
    POST /ops/jobs/{id}/cancel                 Cancel permanently
    POST /ops/jobs/{id}/rerun-validation       Re-run validation

── MAINTENANCE ───────────────────────────────────────────────────────────────
    GET  /ops/maintenance                      Maintenance mode status
    POST /ops/maintenance/enable               Enable maintenance mode
    POST /ops/maintenance/disable              Disable maintenance mode
    POST /ops/maintenance/emergency-stop       EMERGENCY halt

── AUDIT ─────────────────────────────────────────────────────────────────────
    GET  /ops/actions                          All operator actions
    GET  /ops/actions/{resource_id}            Actions for one resource
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.operations.routers import operations

app = FastAPI(
    title="Migration Platform — Operations Console",
    description=(
        "Part 10: The Kubernetes Dashboard equivalent for the Migration Platform. "
        "Gives operators manual control over workers (pause/resume/kill/quarantine/scale), "
        "chunks (reassign/retry/skip), and jobs (pause/resume/cancel/rerun-validation). "
        "Maintenance mode and emergency stop for platform-wide control. "
        "All actions are logged to the immutable operations_actions audit trail "
        "and published to the Event Bus."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(operations.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="operations_console",
                display_name="Operations Console",
                base_url="http://localhost:8016",
                version="1.0.0",
                metadata={
                    "part": 10,
                    "capabilities": [
                        "worker_control",
                        "chunk_control",
                        "job_control",
                        "maintenance_mode",
                        "emergency_stop",
                        "operations_audit",
                    ],
                },
            )
        except Exception as e:
            logger.warning("Service Registry registration failed", error=str(e))

        logger.info("Operations Console started", port=8016)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    # Check Redis connectivity (critical for operations console)
    from backend.shared.config.redis import redis_client
    redis_ok = False
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    # Check maintenance mode
    maintenance = False
    try:
        maintenance = bool(redis_client.exists("migration:maintenance:active"))
    except Exception:
        pass

    return {
        "status":           "ok" if redis_ok else "degraded",
        "service":          "operations_console",
        "port":             8016,
        "version":          "1.0.0",
        "redis":            "ok" if redis_ok else "unavailable",
        "maintenance_mode": maintenance,
        "controls": {
            "worker_control": True,
            "chunk_control":  True,
            "job_control":    True,
            "maintenance":    True,
        },
    }
