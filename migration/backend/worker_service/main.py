"""
Worker Service - Entry Point
File: migration/backend/worker_service/main.py

This is the main entry point for the Worker Service.
Run this file to start a worker process.

Usage:
    python -m backend.worker_service.main

Or from the migration/ directory:
    python backend/worker_service/main.py
"""

import signal
import sys
import os
import uuid

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.worker_service.app.worker import Worker
from backend.shared.config.logging import logger


def handle_shutdown(signum, frame):
    """Handle graceful shutdown on SIGTERM or SIGINT."""
    logger.info("Shutdown signal received. Stopping worker gracefully...")
    sys.exit(0)


def main():
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Generate a unique worker ID for this process
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"

    logger.info("Starting Worker Service", worker_id=worker_id)

    worker = Worker(worker_id=worker_id)
    worker.run()


if __name__ == "__main__":
    main()
