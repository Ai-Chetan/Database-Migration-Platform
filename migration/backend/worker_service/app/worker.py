"""
Updated Worker Service
File: migration/backend/worker_service/app/worker.py

REPLACES the previous worker.py.

The only change from the old worker: calls WorkflowExecutor.execute()
instead of ChunkExecutor.execute(). Everything else — BRPOP loop,
heartbeat thread, graceful shutdown, Redis queue coordination — is
identical. This is exactly the design goal: the Workflow Engine replaces
the execution kernel with zero changes to worker orchestration logic.

Worker states (unchanged): IDLE → BUSY → STOPPING → OFFLINE

CHANGES IN THIS VERSION (Stage 1 schema audit fix):
  This file previously had its OWN inline heartbeat/registration code
  (_register_worker, _start_heartbeat, _heartbeat_loop, _update_status)
  that wrote to worker_heartbeats using columns that don't exist at all
  in the real schema (worker_id, status, registered_at, host, pid — none
  of these are real columns; the actual columns are worker_name,
  worker_status, hostname, cpu_usage, memory_usage). Every one of those
  writes was wrapped in a bare try/except that silently swallowed the
  resulting "column does not exist" error, so the worker would run and
  process chunks correctly, but would NEVER successfully appear in
  worker_heartbeats — meaning the Operations Console's worker list would
  never show it, regardless of how much data it actually migrated.

  Meanwhile, a second, entirely separate and already-CORRECT
  implementation sat unused right next to it:
  worker_service/app/monitoring/heartbeat.py's HeartbeatManager, which
  uses the real column names. This was a Pattern-4 duplicate (two
  systems built for the same purpose) where the working one was already
  written but never actually wired up. Fixed by deleting the broken
  inline heartbeat code and using HeartbeatManager instead.
"""

import os
import json
import time
import signal
import threading
import uuid

from backend.shared.config.database import SessionLocal
from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger
from backend.shared.constants.queues import Queues
from backend.worker_service.app.monitoring.heartbeat import HeartbeatManager

# ── THE KEY CHANGE: import WorkflowExecutor instead of ChunkExecutor ──────────
from backend.workflow_engine.executor.workflow_executor import WorkflowExecutor


WORKER_ID          = os.environ.get("WORKER_ID", f"worker-{uuid.uuid4().hex[:8]}")
QUEUE_TIMEOUT      = int(os.environ.get("QUEUE_TIMEOUT", "5"))
TENANT_ID          = os.environ.get("TENANT_ID", "local")


class Worker:

    def __init__(self):
        self.worker_id = WORKER_ID
        self.running   = True
        self.busy      = False
        self.executor  = WorkflowExecutor(worker_id=self.worker_id)   # ← was ChunkExecutor
        self.heartbeat = HeartbeatManager(worker_id=self.worker_id)

        # Graceful shutdown on SIGTERM/SIGINT
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT,  self._handle_shutdown)

    def start(self):
        logger.info("Worker starting", worker_id=self.worker_id)
        self.heartbeat.start()   # writes STARTING then IDLE, starts background loop

        logger.info("Worker ready — listening for chunks", worker_id=self.worker_id)

        while self.running:
            self.heartbeat.set_idle()

            # ── Check throttle (Resource Governor may have set this) ──────
            allowed_workers  = self._check_throttle()
            if not allowed_workers:
                time.sleep(QUEUE_TIMEOUT)
                continue

            # ── Blocking pop from Redis queue ─────────────────────────────
            try:
                message = redis_client.brpop(
                    [Queues.MIGRATION_QUEUE, Queues.RETRY_QUEUE],
                    timeout=QUEUE_TIMEOUT
                )
            except Exception as e:
                logger.warning("Redis BRPOP failed", error=str(e))
                time.sleep(2)
                continue

            if not message:
                continue   # Timeout, loop back

            if not self.running:
                # Shutting down — push message back and stop
                _, raw = message
                redis_client.lpush(Queues.MIGRATION_QUEUE, raw)
                break

            try:
                _, raw    = message
                payload   = json.loads(raw)
                job_id    = payload["job_id"]
                table_id  = payload["table_id"]
                chunk_id  = payload["chunk_id"]
            except Exception as e:
                logger.error("Malformed queue message", error=str(e))
                continue

            self.busy = True
            self.heartbeat.set_busy(chunk_id)

            db = SessionLocal()
            try:
                # ── THE KEY CHANGE: call WorkflowExecutor ─────────────────
                self.executor.execute(
                    db=db,
                    job_id=job_id,
                    table_id=table_id,
                    chunk_id=chunk_id,
                    tenant_id=TENANT_ID,
                )
            except Exception as e:
                logger.error(
                    "Unhandled error in WorkflowExecutor",
                    error=str(e), chunk_id=chunk_id, worker_id=self.worker_id
                )
            finally:
                db.close()
                self.busy = False

        self.heartbeat.stop()   # writes STOPPING, joins background thread, writes OFFLINE
        logger.info("Worker stopped", worker_id=self.worker_id)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _handle_shutdown(self, signum, frame):
        logger.info("Shutdown signal received, finishing current chunk...",
                    worker_id=self.worker_id)
        self.running = False

    # ── Throttle check ───────────────────────────────────────────────────────

    def _check_throttle(self) -> bool:
        """
        Check if Resource Governor has throttled this worker.
        Returns True if we should pull a new chunk, False if we should wait.
        """
        try:
            # Check for any throttle keys (pattern match not available in BRPOP,
            # so we just check if there's a globally-scoped throttle key in Redis)
            keys = redis_client.keys("migration:throttle:*")
            if not keys:
                return True   # No throttle active

            # There's at least one throttle key — check if we're under the limit
            # In a full implementation, we'd check the specific job's throttle.
            # For MVP, if any throttle is active, pace ourselves.
            allowed = int(redis_client.get(keys[0]) or 4)
            return allowed > 0
        except Exception:
            return True   # Fail open: if throttle check fails, proceed


if __name__ == "__main__":
    Worker().start()
