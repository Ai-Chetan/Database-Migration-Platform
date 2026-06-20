"""
Heartbeat Manager - Worker Health Monitoring
File: migration/backend/worker_service/app/monitoring/heartbeat.py

Workers send a heartbeat to the PostgreSQL metadata database
every 10 seconds. This lets the control plane detect dead workers
and reassign their chunks.

The heartbeat runs in a background daemon thread so it doesn't
block the main chunk execution loop.

Table updated: worker_heartbeats
"""

import threading
import time
import datetime
import psutil
import os

from backend.shared.config.database import get_db
from backend.shared.config.logging import logger


# How often to send a heartbeat (seconds)
HEARTBEAT_INTERVAL = 10


class HeartbeatManager:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.running = False
        self._thread = None
        self._current_chunk_id = None

    def start(self):
        """Start the heartbeat background thread."""
        self.running = True
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,  # Dies when main thread dies
            name=f"heartbeat-{self.worker_id}"
        )
        self._thread.start()
        logger.info("Heartbeat thread started", worker_id=self.worker_id)

    def stop(self):
        """Stop the heartbeat thread."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Heartbeat thread stopped", worker_id=self.worker_id)

    def set_current_chunk(self, chunk_id: str):
        """Called by executor to track which chunk this worker is processing."""
        self._current_chunk_id = chunk_id

    def clear_current_chunk(self):
        """Called when a chunk finishes."""
        self._current_chunk_id = None

    def _heartbeat_loop(self):
        """Background loop that sends heartbeats every HEARTBEAT_INTERVAL seconds."""
        while self.running:
            try:
                self._send_heartbeat()
            except Exception as e:
                # Don't crash the worker if heartbeat fails
                logger.warning(
                    "Heartbeat failed (non-fatal)",
                    worker_id=self.worker_id,
                    error=str(e)
                )
            time.sleep(HEARTBEAT_INTERVAL)

    def _send_heartbeat(self):
        """Write or update this worker's heartbeat row in the database."""
        db = next(get_db())
        try:
            # Get CPU and memory usage for monitoring
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_info = psutil.virtual_memory()
            memory_percent = memory_info.percent
            hostname = os.uname().nodename if hasattr(os, 'uname') else os.environ.get('HOSTNAME', 'unknown')

            # Try to find existing heartbeat row for this worker
            from sqlalchemy import text
            result = db.execute(
                text("SELECT id FROM worker_heartbeats WHERE worker_name = :worker_name"),
                {"worker_name": self.worker_id}
            ).fetchone()

            now = datetime.datetime.utcnow()

            if result:
                # Update existing row
                db.execute(
                    text("""
                        UPDATE worker_heartbeats
                        SET worker_status = :status,
                            current_chunk_id = :chunk_id,
                            cpu_usage = :cpu,
                            memory_usage = :memory,
                            last_heartbeat = :now
                        WHERE worker_name = :worker_name
                    """),
                    {
                        "status": "running",
                        "chunk_id": self._current_chunk_id,
                        "cpu": cpu_percent,
                        "memory": memory_percent,
                        "now": now,
                        "worker_name": self.worker_id
                    }
                )
            else:
                # Insert new row for this worker
                db.execute(
                    text("""
                        INSERT INTO worker_heartbeats
                            (id, worker_name, worker_status, current_chunk_id,
                             hostname, cpu_usage, memory_usage, last_heartbeat, created_at)
                        VALUES
                            (gen_random_uuid(), :worker_name, :status, :chunk_id,
                             :hostname, :cpu, :memory, :now, :now)
                    """),
                    {
                        "worker_name": self.worker_id,
                        "status": "running",
                        "chunk_id": self._current_chunk_id,
                        "hostname": hostname,
                        "cpu": cpu_percent,
                        "memory": memory_percent,
                        "now": now
                    }
                )

            db.commit()

            logger.debug(
                "Heartbeat sent",
                worker_id=self.worker_id,
                cpu=cpu_percent,
                memory=memory_percent
            )

        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()
