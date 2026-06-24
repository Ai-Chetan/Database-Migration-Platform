"""
Prometheus Metrics Registry
File: migration/backend/worker_service/app/monitoring/metrics_registry.py

Central definition of every Prometheus metric in the platform.

Import this module in any file that needs to record a metric.
The registry is module-level so all workers in the same process share it.

Metric naming convention:
    migration_<noun>_<verb>_<unit>

    migration_rows_processed_total        ← Counter (only goes up)
    migration_chunks_completed_total      ← Counter
    migration_worker_cpu_usage_percent    ← Gauge (can go up and down)
    migration_chunk_duration_seconds      ← Histogram (distribution)

Install:
    pip install prometheus-client

Prometheus scrapes metrics from:
    http://localhost:8002/metrics

Start the metrics HTTP server (called from worker main.py):
    from backend.worker_service.app.monitoring.metrics_registry import start_metrics_server
    start_metrics_server(port=8002)
"""

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    start_http_server,
    REGISTRY,
)
from backend.shared.config.logging import logger


# ── Counters (only ever go up) ────────────────────────────────────────────────

# Total rows written to target databases across all workers
rows_processed_total = Counter(
    "migration_rows_processed_total",
    "Total number of rows successfully written to target databases",
    ["worker_id", "table_name"]
)

# Total chunks that completed successfully
chunks_completed_total = Counter(
    "migration_chunks_completed_total",
    "Total number of chunks that completed successfully",
    ["worker_id"]
)

# Total chunks that failed (before retry)
chunks_failed_total = Counter(
    "migration_chunks_failed_total",
    "Total number of chunk failures",
    ["worker_id", "error_type"]
)

# Total retry attempts
chunks_retried_total = Counter(
    "migration_chunks_retried_total",
    "Total number of chunk retry attempts",
    ["worker_id"]
)

# Total checksum validation failures
checksum_failures_total = Counter(
    "migration_checksum_failures_total",
    "Total number of checksum validation failures",
    ["worker_id", "table_name"]
)

# Total validation failures (row count or checksum)
validation_failures_total = Counter(
    "migration_validation_failures_total",
    "Total number of validation failures (row count or checksum)",
    ["worker_id"]
)

# Stale chunks recovered by StaleChunkRecovery
stale_chunks_recovered_total = Counter(
    "migration_stale_chunks_recovered_total",
    "Total number of stale chunks recovered and requeued"
)


# ── Gauges (go up and down) ───────────────────────────────────────────────────

# Current worker CPU usage
worker_cpu_usage = Gauge(
    "migration_worker_cpu_usage_percent",
    "Current CPU usage of the worker process (0-100)",
    ["worker_id"]
)

# Current worker memory usage
worker_memory_usage = Gauge(
    "migration_worker_memory_usage_percent",
    "Current memory usage of the worker process (0-100)",
    ["worker_id"]
)

# How many chunks a worker is currently processing (0 or 1 normally)
worker_active_chunks = Gauge(
    "migration_worker_active_chunks",
    "Number of chunks the worker is currently processing",
    ["worker_id"]
)

# Current Redis queue depth
redis_queue_depth = Gauge(
    "migration_redis_queue_depth",
    "Number of messages currently in the main migration queue"
)

# Current Redis retry queue depth
redis_retry_queue_depth = Gauge(
    "migration_redis_retry_queue_depth",
    "Number of messages currently in the retry queue"
)

# Total active workers (updated by heartbeat)
active_workers_total = Gauge(
    "migration_active_workers_total",
    "Number of currently active (non-stale) workers"
)


# ── Histograms (distribution of values) ──────────────────────────────────────

# How long each chunk takes to execute
chunk_duration_seconds = Histogram(
    "migration_chunk_duration_seconds",
    "Time taken to execute a single chunk (seconds)",
    ["worker_id", "table_name"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800]  # 1s to 30min
)

# Rows per second throughput per chunk
chunk_throughput_rows_per_second = Histogram(
    "migration_chunk_throughput_rows_per_second",
    "Throughput in rows/second for each completed chunk",
    ["worker_id"],
    buckets=[100, 500, 1000, 5000, 10000, 50000, 100000, 500000]
)

# Batch insert latency
batch_insert_latency_seconds = Histogram(
    "migration_batch_insert_latency_seconds",
    "Time taken to insert a single batch into the target database",
    ["worker_id"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)


# ── Metrics HTTP server ───────────────────────────────────────────────────────

_metrics_server_started = False


def start_metrics_server(port: int = 8002):
    """
    Start the Prometheus metrics HTTP server.
    Call this once from main.py before starting the worker.

    Prometheus will scrape: http://localhost:8002/metrics
    """
    global _metrics_server_started
    if _metrics_server_started:
        return

    try:
        start_http_server(port)
        _metrics_server_started = True
        logger.info("Prometheus metrics server started", port=port)
    except OSError as e:
        # Port already in use (another worker on same machine) — that's fine
        logger.warning(
            "Prometheus metrics server port already in use",
            port=port,
            error=str(e)
        )
