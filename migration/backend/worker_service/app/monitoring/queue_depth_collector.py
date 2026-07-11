"""
Queue Depth Collector
File: migration/backend/worker_service/app/monitoring/queue_depth_collector.py

Background thread that polls Redis every 15 seconds and updates
the Prometheus gauges for queue depth.

This runs inside the worker process — no separate service needed.

Usage (in worker.py):
    from backend.worker_service.app.monitoring.queue_depth_collector import QueueDepthCollector
    collector = QueueDepthCollector()
    collector.start()
"""

import threading
import time

from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.shared.config.logging import logger
from backend.worker_service.app.monitoring.metrics_registry import (
    redis_queue_depth,
    redis_retry_queue_depth,
)

COLLECT_INTERVAL = 15  # seconds


class QueueDepthCollector:

    def __init__(self):
        self.running = False
        self._thread = None

    def start(self):
        self.running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="queue-depth-collector"
        )
        self._thread.start()
        logger.info("QueueDepthCollector started")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self.running:
            try:
                main_depth  = redis_client.llen(Queues.MIGRATION_QUEUE)
                retry_depth = redis_client.llen(Queues.RETRY_QUEUE)

                redis_queue_depth.set(main_depth)
                redis_retry_queue_depth.set(retry_depth)

                logger.debug(
                    "Queue depths updated",
                    main=main_depth,
                    retry=retry_depth
                )
            except Exception as e:
                logger.warning("Queue depth collection failed", error=str(e))

            time.sleep(COLLECT_INTERVAL)
