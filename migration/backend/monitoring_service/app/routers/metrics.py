"""
Metrics Router
File: migration/backend/monitoring_service/app/routers/metrics.py

Endpoints:
    GET /jobs/{job_id}/metrics   → throughput, ETA, rows/sec for one job
    GET /metrics                 → platform-wide summary
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.monitoring_service.app.repositories.monitoring_repository import MonitoringRepository
from backend.monitoring_service.app.services.progress_calculator import (
    compute_progress_pct, compute_elapsed_seconds, compute_eta_seconds,
    format_duration, format_throughput
)

router = APIRouter(tags=["Metrics"])
repo   = MonitoringRepository()


@router.get("/jobs/{job_id}/metrics", summary="Get performance metrics for a job")
def get_job_metrics(job_id: str, db: Session = Depends(get_db)):
    """
    Throughput, ETA, and row counts for a single job.

    Response:
    {
      "job_id": "abc-123",
      "rows_processed": 3400000,
      "rows_per_second": 45000,
      "eta_seconds": 36,
      "eta_minutes": 0.6,
      "eta_human": "36s",
      "chunks_completed": 34,
      "chunks_total": 65,
      "chunks_failed": 0,
      "progress_pct": 52.3,
      "elapsed_seconds": 75.6,
      "elapsed_human": "1m 15s",
      "throughput_human": "45,000 rows/sec"
    }
    """
    job = repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    chunk_stats   = repo.get_chunk_stats(db, job_id)
    rows_migrated = repo.get_rows_migrated(db, job_id)
    rows_total    = repo.get_total_rows(db, job_id)
    throughput    = repo.get_throughput_rps(db, job_id)
    elapsed       = compute_elapsed_seconds(job)
    eta           = compute_eta_seconds(rows_total, rows_migrated, throughput)

    total     = chunk_stats.get("total", 0)
    completed = chunk_stats.get("completed", 0)
    failed    = chunk_stats.get("failed", 0)

    return {
        "job_id":          job_id,
        "rows_processed":  rows_migrated,
        "rows_per_second": throughput,
        "eta_seconds":     eta,
        "eta_minutes":     round(eta / 60, 1) if eta else None,
        "eta_human":       format_duration(eta),
        "chunks_completed": completed,
        "chunks_total":    total,
        "chunks_failed":   failed,
        "progress_pct":    compute_progress_pct(completed, total),
        "elapsed_seconds": elapsed,
        "elapsed_human":   format_duration(elapsed),
        "throughput_human": format_throughput(throughput),
    }


@router.get("/metrics", summary="Platform-wide summary metrics")
def get_platform_metrics(db: Session = Depends(get_db)):
    """
    Overview of the entire platform — useful for a top-level dashboard.

    Response:
    {
      "active_jobs": 3,
      "completed_jobs": 12,
      "failed_jobs": 1,
      "total_workers": 4,
      "active_workers": 4,
      "redis_queue_depth": 142,
      "redis_retry_queue_depth": 3,
      "total_rows_migrated": 48500000
    }
    """
    job_counts    = repo.count_jobs_by_status(db)
    active_jobs   = sum(job_counts.get(s, 0) for s in ["running", "pending", "planning"])
    completed_jobs = job_counts.get("completed", 0)
    failed_jobs   = job_counts.get("failed", 0)

    active_workers = repo.count_active_workers(db)
    total_workers  = repo.count_total_workers(db)
    total_rows     = repo.get_total_rows_migrated_all_jobs(db)

    try:
        queue_depth = redis_client.llen(Queues.MIGRATION_QUEUE)
        retry_depth = redis_client.llen(Queues.RETRY_QUEUE)
    except Exception:
        queue_depth = -1
        retry_depth = -1

    return {
        "active_jobs":             active_jobs,
        "completed_jobs":          completed_jobs,
        "failed_jobs":             failed_jobs,
        "total_workers":           total_workers,
        "active_workers":          active_workers,
        "redis_queue_depth":       queue_depth,
        "redis_retry_queue_depth": retry_depth,
        "total_rows_migrated":     total_rows,
    }
