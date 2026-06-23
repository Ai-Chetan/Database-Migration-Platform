"""
Worker — Multi-Worker Ready
File: migration/backend/worker_service/app/worker.py

REPLACES the previous worker.py.

What changed for Priority 6:
    - heartbeat.set_busy(chunk_id) called before each chunk executes
    - heartbeat.set_idle() called after each chunk finishes
    - This means the worker_heartbeats table shows exactly which chunk
      each worker is processing at any moment

    Multi-worker works automatically because:
        BRPOP is atomic — Redis guarantees each message goes to exactly
        one worker. Run 4x `python main.py` and Redis distributes chunks.

Worker lifecycle with status:
    startup        → STARTING
    resume scan    → STARTING
    polling loop   → IDLE  (between chunks)
    executing      → BUSY  (during chunk)
    shutdown       → STOPPING → OFFLINE
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

        # 1. Heartbeat — marks worker STARTING in DB immediately
        self.heartbeat.start()

        # 2. Resume scan — pushes unfinished chunks back to Redis
        #    Only one worker needs to do this, but running it on all workers
        #    is safe because ResumeManager is idempotent (checks status before pushing)
        logger.info("Running resume scan...", worker_id=self.worker_id)
        db = next(get_db())
        try:
            self.resume_manager.resume_incomplete_jobs(db)
        finally:
            db.close()

        # 3. Stale chunk recovery background thread
        self.stale_recovery.start()

        # 4. Main polling loop
        logger.info("Worker ready — polling queue", worker_id=self.worker_id)
        while self.running:
            try:
                self._poll_and_process()
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt", worker_id=self.worker_id)
                self.running = False
            except Exception as e:
                logger.error("Unexpected error in worker loop", error=str(e), worker_id=self.worker_id)
                time.sleep(3)

        # 5. Graceful shutdown
        self.stale_recovery.stop()
        self.heartbeat.stop()          # writes OFFLINE to DB
        logger.info("Worker stopped", worker_id=self.worker_id)

    def _poll_and_process(self):
        """
        Block on Redis for up to 5 seconds.
        If a message arrives, execute the chunk and track heartbeat state.
        """
        # BRPOP: atomic blocking pop — exactly one worker gets each message
        result = redis_client.brpop(Queues.MIGRATION_QUEUE, timeout=5)

        if result is None:
            # No message — stay IDLE, loop again
            return

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

        # ── Mark BUSY before executing ────────────────────────────────────
        self.heartbeat.set_busy(chunk_id=chunk_id)

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
            # ── Back to IDLE after chunk (success or failure) ─────────────
            self.heartbeat.set_idle()