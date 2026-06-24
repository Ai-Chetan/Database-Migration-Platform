"""
Enhanced Heartbeat Manager
File: migration/backend/worker_service/app/monitoring/heartbeat.py

REPLACES previous heartbeat.py completely.

Tracks full worker state: STARTING / IDLE / BUSY / STOPPING / OFFLINE
Each worker writes to worker_heartbeats table every 10 seconds.
Multiple workers write their OWN rows — no conflicts.

This is what makes the /workers dashboard endpoint work.
"""

import threading
import time
import datetime
import os
import socket

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from sqlalchemy import text
from backend.shared.config.database import get_db
from backend.shared.config.logging import logger

HEARTBEAT_INTERVAL = 10  # seconds


class WorkerStatus:
    STARTING = "STARTING"
    IDLE     = "IDLE"
    BUSY     = "BUSY"
    STOPPING = "STOPPING"
    OFFLINE  = "OFFLINE"


class HeartbeatManager:

    def __init__(self, worker_id: str):
        self.worker_id   = worker_id
        self.running     = False
        self._thread     = None
        self._status     = WorkerStatus.STARTING
        self._chunk_id   = None
        self._hostname   = self._get_hostname()
        self._pid        = os.getpid()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Call once on worker startup. Immediately writes STARTING to DB."""
        self.running = True
        self._status = WorkerStatus.STARTING
        self._write()
        self._status = WorkerStatus.IDLE

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"heartbeat-{self.worker_id}"
        )
        self._thread.start()
        logger.info("Heartbeat started", worker_id=self.worker_id, hostname=self._hostname)

    def stop(self):
        """Call on graceful shutdown. Marks worker OFFLINE."""
        self._status = WorkerStatus.STOPPING
        self._write()
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._status = WorkerStatus.OFFLINE
        self._write()
        logger.info("Worker marked OFFLINE", worker_id=self.worker_id)

    def set_busy(self, chunk_id: str):
        """Call at the START of executing a chunk."""
        self._status   = WorkerStatus.BUSY
        self._chunk_id = chunk_id

    def set_idle(self):
        """Call when a chunk finishes (success or failure)."""
        self._status   = WorkerStatus.IDLE
        self._chunk_id = None

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self):
        while self.running:
            try:
                self._write()
            except Exception as e:
                logger.warning("Heartbeat write failed", error=str(e))
            time.sleep(HEARTBEAT_INTERVAL)

    def _write(self):
        cpu = self._cpu()
        mem = self._memory()
        now = datetime.datetime.utcnow()

        db = next(get_db())
        try:
            row = db.execute(
                text("SELECT id FROM worker_heartbeats WHERE worker_name = :n"),
                {"n": self.worker_id}
            ).fetchone()

            if row:
                db.execute(
                    text("""
                        UPDATE worker_heartbeats
                        SET worker_status    = :status,
                            current_chunk_id = :chunk_id,
                            cpu_usage        = :cpu,
                            memory_usage     = :mem,
                            last_heartbeat   = :now,
                            hostname         = :host
                        WHERE worker_name = :n
                    """),
                    {"status": self._status, "chunk_id": self._chunk_id,
                     "cpu": cpu, "mem": mem, "now": now,
                     "host": self._hostname, "n": self.worker_id}
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO worker_heartbeats
                            (id, worker_name, worker_status, current_chunk_id,
                             hostname, cpu_usage, memory_usage, last_heartbeat, created_at)
                        VALUES
                            (gen_random_uuid(), :n, :status, :chunk_id,
                             :host, :cpu, :mem, :now, :now)
                    """),
                    {"n": self.worker_id, "status": self._status,
                     "chunk_id": self._chunk_id, "host": self._hostname,
                     "cpu": cpu, "mem": mem, "now": now}
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _cpu(self) -> float:
        if PSUTIL_AVAILABLE:
            try:
                return psutil.cpu_percent(interval=None)
            except Exception:
                pass
        return 0.0

    def _memory(self) -> float:
        if PSUTIL_AVAILABLE:
            try:
                return psutil.virtual_memory().percent
            except Exception:
                pass
        return 0.0

    def _get_hostname(self) -> str:
        try:
            return socket.gethostname()
        except Exception:
            return os.environ.get("HOSTNAME", "unknown")
