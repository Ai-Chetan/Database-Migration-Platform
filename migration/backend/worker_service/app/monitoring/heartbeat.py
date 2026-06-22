"""
Heartbeat Manager — Updated for Multi-Worker Support
File: migration/backend/worker_service/app/monitoring/heartbeat.py

REPLACES the previous heartbeat.py.

What changed:
    - Tracks worker_state: STARTING → IDLE → BUSY → STOPPING → OFFLINE
    - Records hostname, pid, started_at
    - Exposes set_busy() / set_idle() so ChunkExecutor can flip state
    - Uses UPSERT pattern (INSERT ... ON CONFLICT UPDATE) so multiple
      workers writing to the same table never clash

The worker_heartbeats table columns used:
    worker_name, worker_status, current_chunk_id,
    hostname, cpu_usage, memory_usage, last_heartbeat, created_at
"""

import threading
import time
import datetime
import os

from backend.shared.config.database import get_db
from backend.shared.config.logging import logger

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

HEARTBEAT_INTERVAL = 10   # seconds between heartbeat writes


class WorkerState:
    STARTING = "STARTING"
    IDLE     = "IDLE"
    BUSY     = "BUSY"
    STOPPING = "STOPPING"
    OFFLINE  = "OFFLINE"


class HeartbeatManager:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.running    = False
        self._thread    = None
        self._state     = WorkerState.STARTING
        self._chunk_id  = None
        self._lock      = threading.Lock()
        self._hostname  = self._get_hostname()
        self._pid       = os.getpid()
        self._started_at = datetime.datetime.utcnow()

    # ── Public API called by ChunkExecutor ───────────────────────────────

    def set_busy(self, chunk_id: str):
        """Call when a worker starts processing a chunk."""
        with self._lock:
            self._state    = WorkerState.BUSY
            self._chunk_id = chunk_id

    def set_idle(self):
        """Call when a worker finishes a chunk and is waiting for the next."""
        with self._lock:
            self._state    = WorkerState.IDLE
            self._chunk_id = None

    def set_stopping(self):
        """Call during graceful shutdown."""
        with self._lock:
            self._state    = WorkerState.STOPPING
            self._chunk_id = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        self.running = True
        self._state  = WorkerState.IDLE
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"heartbeat-{self.worker_id}"
        )
        self._thread.start()
        logger.info("Heartbeat started", worker_id=self.worker_id)

    def stop(self):
        self.set_stopping()
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._write_offline()
        logger.info("Heartbeat stopped", worker_id=self.worker_id)

    # ── Internal ──────────────────────────────────────────────────────────

    def _loop(self):
        while self.running:
            try:
                self._write_heartbeat()
            except Exception as e:
                logger.warning("Heartbeat write failed (non-fatal)", error=str(e))
            time.sleep(HEARTBEAT_INTERVAL)

    def _write_heartbeat(self):
        with self._lock:
            state    = self._state
            chunk_id = self._chunk_id

        cpu_pct, mem_pct = self._get_system_stats()
        now = datetime.datetime.utcnow()

        db = next(get_db())
        try:
            from sqlalchemy import text
            # UPSERT: insert new row or update existing one for this worker_name
            db.execute(text("""
                INSERT INTO worker_heartbeats
                    (id, worker_name, worker_status, current_chunk_id,
                     hostname, cpu_usage, memory_usage, last_heartbeat, created_at)
                VALUES
                    (gen_random_uuid(), :name, :status, :chunk_id,
                     :hostname, :cpu, :mem, :now, :now)
                ON CONFLICT (worker_name)
                DO UPDATE SET
                    worker_status    = EXCLUDED.worker_status,
                    current_chunk_id = EXCLUDED.current_chunk_id,
                    cpu_usage        = EXCLUDED.cpu_usage,
                    memory_usage     = EXCLUDED.memory_usage,
                    last_heartbeat   = EXCLUDED.last_heartbeat
            """), {
                "name":     self.worker_id,
                "status":   state,
                "chunk_id": str(chunk_id) if chunk_id else None,
                "hostname": self._hostname,
                "cpu":      cpu_pct,
                "mem":      mem_pct,
                "now":      now,
            })
            db.commit()
            logger.debug("Heartbeat written",
                         worker_id=self.worker_id,
                         state=state,
                         cpu=cpu_pct)
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def _write_offline(self):
        """Mark this worker OFFLINE when it shuts down cleanly."""
        db = next(get_db())
        try:
            from sqlalchemy import text
            db.execute(text("""
                UPDATE worker_heartbeats
                SET worker_status = 'OFFLINE', last_heartbeat = :now
                WHERE worker_name = :name
            """), {"name": self.worker_id, "now": datetime.datetime.utcnow()})
            db.commit()
        except Exception:
            pass
        finally:
            db.close()

    def _get_system_stats(self):
        if not HAS_PSUTIL:
            return 0.0, 0.0
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            return cpu, mem
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def _get_hostname():
        try:
            return os.uname().nodename
        except Exception:
            return os.environ.get("HOSTNAME", "unknown")