"""
Worker Entry Point — with Prometheus Metrics
File: migration/backend/worker_service/main.py

Changes:
    1. Starts Prometheus metrics HTTP server on port 8002
       Prometheus scrapes:  http://localhost:8002/metrics
    2. Starts QueueDepthCollector background thread
    3. Everything else unchanged

Ports used by the full platform:
    8000 → Control Plane API (future)
    8001 → Monitoring Service API
    8002 → Prometheus metrics (this file, per worker)
    5432 → PostgreSQL
    6379 → Redis
"""

import signal
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.worker_service.app.worker import Worker
from backend.worker_service.app.monitoring.metrics_registry import start_metrics_server
from backend.worker_service.app.monitoring.queue_depth_collector import QueueDepthCollector
from backend.shared.config.logging import logger

_worker_instance = None


def handle_shutdown(signum, frame):
    logger.info("Shutdown signal received")
    sys.exit(0)


def main():
    global _worker_instance

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    logger.info("Starting worker", worker_id=worker_id)

    # Start Prometheus metrics HTTP server
    # If multiple workers run on the same machine, only the first one
    # will bind port 8002 — the others silently skip (handled in metrics_registry.py)
    start_metrics_server(port=8002)

    # Start background thread that polls Redis queue depth → Prometheus gauges
    collector = QueueDepthCollector()
    collector.start()

    # Start the worker
    _worker_instance = Worker(worker_id=worker_id)
    _worker_instance.run()


if __name__ == "__main__":
    main()
