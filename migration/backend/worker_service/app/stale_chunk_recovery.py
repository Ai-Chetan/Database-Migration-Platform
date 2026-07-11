"""
Stale Chunk Recovery — Priority 2
File: migration/backend/worker_service/app/stale_chunk_recovery.py

Problem it solves:
    A worker picks up chunk #47 and marks it RUNNING.
    That worker process then crashes (OOM kill, Docker stop, power loss).
    Chunk #47 stays RUNNING forever.
    The job never completes.

Solution:
    A background thread runs every 60 seconds.
    It queries for chunks that are RUNNING but whose last_heartbeat
    is older than 15 minutes — meaning the worker that owned them is dead.
    Those chunks are marked FAILED and requeued.

How heartbeat works:
    The HeartbeatManager (heartbeat.py) updates last_heartbeat every 10 seconds
    while a worker is alive. If 15 minutes pass with no update, the worker is dead.

This runs INSIDE the worker process as a daemon thread.
Every worker participates in recovery — no separate process needed.

Usage (added to worker.py):
    from backend.worker_service.app.stale_chunk_recovery import StaleChunkRecovery

    recovery = StaleChunkRecovery()
    recovery.start()   # starts background thread
    ...
    recovery.stop()    # on shutdown
"""

import threading
import time
import datetime
import json
from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.control_plane.app.models.migration import MigrationChunk
from backend.shared.config.database import get_db
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.shared.config.logging import logger


# Check for stale chunks every this many seconds
RECOVERY_INTERVAL_SECONDS = 60

# A chunk is stale if its last_heartbeat is older than this
STALE_THRESHOLD_MINUTES = 15


class StaleChunkRecovery:
    """
    Background thread that continuously monitors for stuck chunks
    and requeues them for retry.
    """

    def __init__(self):
        self.running = False
        self._thread = None

    def start(self):
        """Start the stale chunk recovery background thread."""
        self.running = True
        self._thread = threading.Thread(
            target=self._recovery_loop,
            daemon=True,
            name="stale-chunk-recovery"
        )
        self._thread.start()
        logger.info("StaleChunkRecovery: Background thread started")

    def stop(self):
        """Stop the recovery thread gracefully."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("StaleChunkRecovery: Background thread stopped")

    def _recovery_loop(self):
        """Runs forever, scanning for stale chunks every 60 seconds."""
        while self.running:
            try:
                self._run_recovery_scan()
            except Exception as e:
                # Never crash the recovery thread — just log and continue
                logger.error(
                    "StaleChunkRecovery: Scan failed (non-fatal)",
                    error=str(e)
                )
            time.sleep(RECOVERY_INTERVAL_SECONDS)

    def _run_recovery_scan(self):
        """
        The core scan logic.

        Query:
            SELECT * FROM migration_chunks
            WHERE status = 'running'
            AND last_heartbeat < NOW() - INTERVAL '15 minutes'

        For each stale chunk:
            1. Increment retry_count
            2. Check if under max_retries
            3. If yes → status = 'pending', release worker, requeue
            4. If no  → status = 'failed', write error message
        """
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=STALE_THRESHOLD_MINUTES
        )

        db: Session = next(get_db())

        try:
            # Find all stale RUNNING chunks
            stale_chunks = db.query(MigrationChunk).filter(
                and_(
                    MigrationChunk.status == "running",
                    # Heartbeat is old OR missing entirely
                    (MigrationChunk.last_heartbeat < stale_threshold) |
                    (MigrationChunk.last_heartbeat == None)
                )
            ).all()

            if not stale_chunks:
                logger.debug("StaleChunkRecovery: No stale chunks found")
                return

            logger.warning(
                "StaleChunkRecovery: Found stale chunks",
                count=len(stale_chunks)
            )

            recovered = 0
            permanently_failed = 0

            for chunk in stale_chunks:
                chunk_id = str(chunk.id)
                job_id = str(chunk.job_id)
                table_id = str(chunk.table_id)

                # Increment retry count
                chunk.retry_count = (chunk.retry_count or 0) + 1
                chunk.worker_id = None  # Release ownership
                chunk.last_error = (
                    f"Stale chunk recovery: worker did not send heartbeat "
                    f"for >{STALE_THRESHOLD_MINUTES} minutes. "
                    f"Retry {chunk.retry_count}/{chunk.max_retries}"
                )

                if chunk.retry_count <= chunk.max_retries:
                    # Still under retry limit → requeue
                    chunk.status = "pending"
                    db.commit()

                    self._requeue(
                        job_id=job_id,
                        table_id=table_id,
                        chunk_id=chunk_id
                    )
                    recovered += 1

                    logger.info(
                        "StaleChunkRecovery: Chunk recovered and requeued",
                        chunk_id=chunk_id,
                        retry_count=chunk.retry_count,
                        max_retries=chunk.max_retries
                    )

                else:
                    # Exceeded max retries → permanently fail
                    chunk.status = "failed"
                    chunk.last_error = (
                        f"Stale chunk permanently failed: "
                        f"exceeded max retries ({chunk.max_retries})"
                    )
                    db.commit()
                    permanently_failed += 1

                    logger.warning(
                        "StaleChunkRecovery: Chunk permanently failed",
                        chunk_id=chunk_id,
                        retry_count=chunk.retry_count,
                        max_retries=chunk.max_retries
                    )

            logger.info(
                "StaleChunkRecovery: Scan complete",
                recovered=recovered,
                permanently_failed=permanently_failed
            )

        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()

    def _requeue(self, job_id: str, table_id: str, chunk_id: str):
        """Push the chunk back onto the main migration queue."""
        message = {
            "job_id": job_id,
            "table_id": table_id,
            "chunk_id": chunk_id,
            "priority": 1
        }
        redis_client.lpush(Queues.MIGRATION_QUEUE, json.dumps(message))

    def run_once(self, db: Session) -> dict:
        """
        Run a single recovery scan synchronously.
        Useful for testing and manual recovery runs.

        Returns a summary dict.
        """
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=STALE_THRESHOLD_MINUTES
        )

        stale_chunks = db.query(MigrationChunk).filter(
            and_(
                MigrationChunk.status == "running",
                (MigrationChunk.last_heartbeat < stale_threshold) |
                (MigrationChunk.last_heartbeat == None)
            )
        ).all()

        recovered = 0
        failed = 0

        for chunk in stale_chunks:
            chunk.retry_count = (chunk.retry_count or 0) + 1
            chunk.worker_id = None

            if chunk.retry_count <= chunk.max_retries:
                chunk.status = "pending"
                db.commit()
                self._requeue(str(chunk.job_id), str(chunk.table_id), str(chunk.id))
                recovered += 1
            else:
                chunk.status = "failed"
                chunk.last_error = "Exceeded max retries during stale recovery"
                db.commit()
                failed += 1

        return {
            "stale_chunks_found": len(stale_chunks),
            "recovered": recovered,
            "permanently_failed": failed
        }
