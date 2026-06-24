"""
Worker — Multi-Worker Ready
File: migration/backend/worker_service/app/worker.py

REPLACES previous worker.py.

Multi-worker works automatically because Redis BRPOP is atomic.
Each BRPOP call returns exactly ONE message to exactly ONE worker.
Run 4 terminals with `python main.py` — chunks distribute automatically.

Worker state tracking added:
    heartbeat.set_busy(chunk_id)  → called before executing each chunk
    heartbeat.set_idle()          → called after each chunk finishes
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
        self.worker_id      = worker_id
        self.running        = True
        self.executor       = ChunkExecutor(worker_id=worker_id)
        self.heartbeat      = HeartbeatManager(worker_id=worker_id)
        self.resume_manager = ResumeManager()
        self.stale_recovery = StaleChunkRecovery()

    def run(self):
        logger.info("Worker starting", worker_id=self.worker_id)

        # Step 1: Register in DB immediately (status = STARTING → IDLE)
        self.heartbeat.start()

        # Step 2: Resume any incomplete jobs from before restart
        db = next(get_db())
        try:
            self.resume_manager.resume_incomplete_jobs(db)
        finally:
            db.close()

        # Step 3: Start background stale-chunk recovery thread
        self.stale_recovery.start()

        # Step 4: Main polling loop
        logger.info("Worker IDLE — polling queue", worker_id=self.worker_id)
        while self.running:
            try:
                self._poll_and_process()
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                logger.error("Worker loop error", error=str(e), worker_id=self.worker_id)
                time.sleep(3)

        # Step 5: Graceful shutdown — mark OFFLINE in DB
        self.stale_recovery.stop()
        self.heartbeat.stop()
        logger.info("Worker stopped cleanly", worker_id=self.worker_id)

    def _poll_and_process(self):
        # BRPOP blocks up to 5 seconds waiting for a message.
        # Atomic: exactly one worker gets each message, no duplicates.
        result = redis_client.brpop(Queues.MIGRATION_QUEUE, timeout=5)

        if result is None:
            return  # timeout — loop again, stays IDLE

        _, raw = result

        try:
            message = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Bad queue message", raw=str(raw), error=str(e))
            return

        job_id   = message.get("job_id")
        table_id = message.get("table_id")
        chunk_id = message.get("chunk_id")

        if not all([job_id, table_id, chunk_id]):
            logger.error("Message missing fields", message=message)
            return

        # Mark BUSY so the /workers dashboard shows what's running
        self.heartbeat.set_busy(chunk_id=chunk_id)

        logger.info("Chunk received", worker_id=self.worker_id,
                    chunk_id=chunk_id, job_id=job_id)

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
            self.heartbeat.set_idle()  # Back to IDLE whether success or failure
