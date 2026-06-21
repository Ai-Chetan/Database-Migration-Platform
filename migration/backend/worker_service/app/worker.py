"""
Worker — Updated with Reliability Layer
File: migration/backend/worker_service/app/worker.py

This replaces the previous worker.py.

What changed from the original:
    1. On startup, runs ResumeManager.resume_incomplete_jobs()
       → Finds all PENDING/RUNNING/RETRYING chunks from before the restart
       → Pushes them back to Redis so they get processed
    2. Starts StaleChunkRecovery background thread
       → Every 60 seconds, scans for chunks stuck in RUNNING
       → Requeues them so the job can complete
    3. Everything else stays the same (BRPOP loop, ChunkExecutor)

The startup sequence:
    1. Register heartbeat
    2. Run resume scan (blocks until complete)
    3. Start stale chunk recovery thread (background)
    4. Start polling loop (blocks forever)
"""

import time
import json

from backend.shared.config.redis import redis_client
from backend.shared.config.database import get_db
from backend.shared.config.logging import logger
from backend.shared.constants.queues import Queues
from backend.worker_service.app.executor.chunk_executor import ChunkExecutor
from backend.worker_service.app.monitoring.heartbeat import HeartbeatManager
from backend.worker_service.app.resume_manager import ResumeManager
from backend.worker_service.app.stale_chunk_recovery import StaleChunkRecovery


class Worker:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.running = True
        self.executor = ChunkExecutor(worker_id=worker_id)
        self.heartbeat = HeartbeatManager(worker_id=worker_id)
        self.resume_manager = ResumeManager()
        self.stale_recovery = StaleChunkRecovery()

    def run(self):
        """
        Full worker lifecycle:
            startup → resume → stale detection → polling loop → shutdown
        """
        logger.info("Worker starting", worker_id=self.worker_id)

        # ── 1. Start heartbeat ────────────────────────────────────────────
        self.heartbeat.start()

        # ── 2. Resume incomplete jobs ─────────────────────────────────────
        # This is the most important startup step.
        # Scans PostgreSQL for any chunks that were in progress when the
        # previous worker process died. Pushes them back to Redis.
        logger.info("Running resume scan on startup...", worker_id=self.worker_id)
        db = next(get_db())
        try:
            self.resume_manager.resume_incomplete_jobs(db)
        finally:
            db.close()
        logger.info("Resume scan complete", worker_id=self.worker_id)

        # ── 3. Start stale chunk recovery background thread ───────────────
        # Runs every 60 seconds in the background.
        # Finds RUNNING chunks with stale heartbeats and requeues them.
        self.stale_recovery.start()

        # ── 4. Main polling loop ──────────────────────────────────────────
        logger.info("Entering main polling loop", worker_id=self.worker_id)
        while self.running:
            try:
                self._poll_and_process()
            except KeyboardInterrupt:
                logger.info("Worker interrupted", worker_id=self.worker_id)
                self.running = False
            except Exception as e:
                logger.error(
                    "Unexpected error in worker loop",
                    worker_id=self.worker_id,
                    error=str(e)
                )
                time.sleep(3)

        # ── 5. Graceful shutdown ──────────────────────────────────────────
        self.stale_recovery.stop()
        self.heartbeat.stop()
        logger.info("Worker stopped", worker_id=self.worker_id)

    def _poll_and_process(self):
        """
        Block on Redis for up to 5 seconds waiting for a chunk message.
        When one arrives, execute it.
        """
        result = redis_client.brpop(Queues.MIGRATION_QUEUE, timeout=5)

        if result is None:
            return

        _, raw_message = result

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse queue message", raw=str(raw_message), error=str(e))
            return

        job_id = message.get("job_id")
        table_id = message.get("table_id")
        chunk_id = message.get("chunk_id")

        if not all([job_id, table_id, chunk_id]):
            logger.error("Invalid message format", message=message)
            return

        logger.info(
            "Processing chunk",
            worker_id=self.worker_id,
            chunk_id=chunk_id,
            job_id=job_id
        )

        db = next(get_db())
        try:
            self.executor.execute(
                db=db,
                job_id=job_id,
                table_id=table_id,
                chunk_id=chunk_id
            )
        finally:
            db.close()
