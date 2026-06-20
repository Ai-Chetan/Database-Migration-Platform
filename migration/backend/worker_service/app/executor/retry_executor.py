"""
Retry Executor - Retry Decision Logic
File: migration/backend/worker_service/app/executor/retry_executor.py

Decides whether a failed chunk should be retried.

Retry Policy:
  - Network errors        → retry
  - Deadlocks             → retry
  - Timeouts              → retry
  - Row count mismatch    → retry (may be transient)
  - Schema errors         → DO NOT retry (will always fail)
  - Max retries exceeded  → DO NOT retry
"""

from backend.control_plane.app.models.migration import MigrationChunk
from backend.shared.config.logging import logger


# Error messages that indicate a permanent failure (no point retrying)
PERMANENT_FAILURE_SIGNALS = [
    "column",
    "schema",
    "does not exist",
    "unknown column",
    "table doesn't exist",
    "syntax error",
    "permission denied",
    "access denied",
    "unsafe conversion",
]


def should_retry(chunk: MigrationChunk) -> bool:
    """
    Returns True if the chunk should be requeued for retry.
    Returns False if the failure is permanent or max retries exceeded.
    """
    # Hard stop: max retries reached
    if chunk.retry_count >= chunk.max_retries:
        logger.warning(
            "Chunk reached max retries, will not retry",
            chunk_id=str(chunk.id),
            retry_count=chunk.retry_count,
            max_retries=chunk.max_retries
        )
        return False

    # Check if the error is a permanent (non-retryable) failure
    error_message = (chunk.last_error or "").lower()
    for signal in PERMANENT_FAILURE_SIGNALS:
        if signal in error_message:
            logger.warning(
                "Permanent failure detected, will not retry",
                chunk_id=str(chunk.id),
                error=chunk.last_error
            )
            return False

    # All other errors: retry
    logger.info(
        "Chunk eligible for retry",
        chunk_id=str(chunk.id),
        retry_count=chunk.retry_count,
        max_retries=chunk.max_retries
    )
    return True


def get_retry_delay_seconds(retry_count: int) -> int:
    """
    Exponential backoff for retry delays.
    retry_count=1 → 5s
    retry_count=2 → 10s
    retry_count=3 → 20s
    retry_count=4 → 40s
    retry_count=5 → 80s
    """
    base_delay = 5
    return base_delay * (2 ** (retry_count - 1))
