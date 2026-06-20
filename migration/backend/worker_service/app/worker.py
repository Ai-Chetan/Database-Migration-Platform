"""
Worker - Core Worker Loop
File: migration/backend/worker_service/app/worker.py

This is the heart of the worker service.
It runs an infinite loop, consuming chunk messages from Redis
and passing them to the ChunkExecutor for processing.
"""

import time
import json
from backend.shared.config.redis import redis_client
from backend.shared.config.database import get_db
from backend.shared.config.logging import logger
from backend.shared.constants.queues import Queues
from backend.worker_service.app.executor.chunk_executor import ChunkExecutor
from backend.worker_service.app.monitoring.heartbeat import HeartbeatManager


class Worker:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.running = True
        self.executor = ChunkExecutor(worker_id=worker_id)
        self.heartbeat = HeartbeatManager(worker_id=worker_id)

    def run(self):
        """
        Main worker loop.
        Continuously polls Redis for chunk messages and processes them.
        """
        logger.info("Worker loop started", worker_id=self.worker_id)

        # Start heartbeat in the background (updates DB every 10s)
        self.heartbeat.start()

        while self.running:
            try:
                self._poll_and_process()
            except KeyboardInterrupt:
                logger.info("Worker interrupted by user", worker_id=self.worker_id)
                self.running = False
            except Exception as e:
                # Log unexpected errors but keep the worker alive
                logger.error(
                    "Unexpected error in worker loop",
                    worker_id=self.worker_id,
                    error=str(e)
                )
                time.sleep(3)  # Brief pause before retrying

        self.heartbeat.stop()
        logger.info("Worker stopped", worker_id=self.worker_id)

    def _poll_and_process(self):
        """
        Poll Redis for one message and process it.
        Uses BRPOP which BLOCKS for up to 5 seconds waiting for a message.
        This is much better than busy-looping with rpop.
        """
        # BRPOP blocks until a message arrives or timeout expires
        # Returns: (queue_name, message_bytes) or None if timeout
        result = redis_client.brpop(Queues.MIGRATION_QUEUE, timeout=5)

        if result is None:
            # No message in 5 seconds - this is normal, just loop again
            logger.debug("No messages in queue, waiting...", worker_id=self.worker_id)
            return

        _, raw_message = result

        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse queue message",
                worker_id=self.worker_id,
                raw=str(raw_message),
                error=str(e)
            )
            return

        job_id = message.get("job_id")
        table_id = message.get("table_id")
        chunk_id = message.get("chunk_id")

        if not all([job_id, table_id, chunk_id]):
            logger.error(
                "Invalid message format - missing required fields",
                worker_id=self.worker_id,
                message=message
            )
            return

        logger.info(
            "Received chunk message",
            worker_id=self.worker_id,
            job_id=job_id,
            chunk_id=chunk_id
        )

        # Get a DB session and execute the chunk
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
