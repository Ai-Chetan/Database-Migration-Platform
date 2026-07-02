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
"""

import os
import json
import time
import signal
import threading
import datetime
import uuid
from sqlalchemy.orm import Session

from backend.shared.config.database import SessionLocal
from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger
from backend.shared.constants.queues import Queues

# ── THE KEY CHANGE: import WorkflowExecutor instead of ChunkExecutor ──────────
from backend.workflow_engine.executor.workflow_executor import WorkflowExecutor


WORKER_ID          = os.environ.get("WORKER_ID", f"worker-{uuid.uuid4().hex[:8]}")
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "10"))
QUEUE_TIMEOUT      = int(os.environ.get("QUEUE_TIMEOUT", "5"))
TENANT_ID          = os.environ.get("TENANT_ID", "local")


class Worker:

    def __init__(self):
        self.worker_id = WORKER_ID
        self.running   = True
        self.busy      = False
        self.executor  = WorkflowExecutor(worker_id=self.worker_id)   # ← was ChunkExecutor
        self._heartbeat_thread: threading.Thread = None

        # Graceful shutdown on SIGTERM/SIGINT
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT,  self._handle_shutdown)

    def start(self):
        logger.info("Worker starting", worker_id=self.worker_id)
        self._register_worker()
        self._start_heartbeat()

        logger.info("Worker ready — listening for chunks", worker_id=self.worker_id)

        while self.running:
            self._update_status("IDLE")

            # ── Check throttle (Resource Governor may have set this) ──────
            throttle_key     = f"migration:throttle:*"
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
            self._update_status("BUSY")

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

        self._update_status("OFFLINE")
        logger.info("Worker stopped", worker_id=self.worker_id)

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _handle_shutdown(self, signum, frame):
        logger.info("Shutdown signal received, finishing current chunk...",
                    worker_id=self.worker_id)
        self.running = False
        self._update_status("STOPPING")

    # ── Registration and heartbeat ────────────────────────────────────────────

    def _register_worker(self):
        db = SessionLocal()
        try:
            from sqlalchemy import text
            db.execute(
                text("""
                    INSERT INTO worker_heartbeats
                        (worker_id, status, last_heartbeat, registered_at, host, pid)
                    VALUES (:wid, 'IDLE', :now, :now, :host, :pid)
                    ON CONFLICT (worker_id)
                    DO UPDATE SET status='IDLE', last_heartbeat=:now
                """),
                {
                    "wid":  self.worker_id,
                    "now":  datetime.datetime.utcnow(),
                    "host": os.environ.get("HOSTNAME", "localhost"),
                    "pid":  os.getpid(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Worker registration failed", error=str(e))
        finally:
            db.close()

    def _start_heartbeat(self):
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="worker-heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while self.running:
            time.sleep(HEARTBEAT_INTERVAL)
            try:
                db = SessionLocal()
                from sqlalchemy import text
                db.execute(
                    text("""
                        UPDATE worker_heartbeats
                        SET last_heartbeat=:now, status=:status
                        WHERE worker_id=:wid
                    """),
                    {
                        "now":    datetime.datetime.utcnow(),
                        "status": "BUSY" if self.busy else "IDLE",
                        "wid":    self.worker_id,
                    }
                )
                db.commit()
                db.close()
            except Exception as e:
                logger.warning("Heartbeat failed", error=str(e))

    def _update_status(self, status: str):
        try:
            db = SessionLocal()
            from sqlalchemy import text
            db.execute(
                text("UPDATE worker_heartbeats SET status=:s, last_heartbeat=:now WHERE worker_id=:wid"),
                {"s": status, "now": datetime.datetime.utcnow(), "wid": self.worker_id}
            )
            db.commit()
            db.close()
        except Exception:
            pass

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
