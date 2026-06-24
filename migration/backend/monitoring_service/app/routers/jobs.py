"""
Jobs Router
File: migration/backend/monitoring_service/app/routers/jobs.py

Endpoints:
    GET /jobs               → list all jobs with summary
    GET /jobs/{job_id}      → full detail for one job
    GET /jobs/{job_id}/tables  → per-table breakdown
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.shared.config.database import get_db
from backend.monitoring_service.app.repositories.monitoring_repository import MonitoringRepository
from backend.monitoring_service.app.services.progress_calculator import (
    build_job_detail, compute_progress_pct
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])
repo   = MonitoringRepository()


@router.get("", summary="List all migration jobs")
def list_jobs(db: Session = Depends(get_db)):
    """
    Returns all jobs ordered by created_at descending.

    Response:
    [
      {
        "job_id": "abc-123",
        "status": "running",
        "progress_pct": 56.2,
        "completed_chunks": 281,
        "total_chunks": 500,
        ...
      }
    ]
    """
    jobs = repo.get_all_jobs(db)
    result = []
    for job in jobs:
        chunk_stats  = repo.get_chunk_stats(db, str(job.id))
        total        = chunk_stats.get("total", 0)
        completed    = chunk_stats.get("completed", 0)
        failed       = chunk_stats.get("failed", 0)

        result.append({
            "job_id":           str(job.id),
            "status":           job.status,
            "tenant_id":        job.tenant_id,
            "total_chunks":     job.total_chunks or 0,
            "completed_chunks": job.completed_chunks or 0,
            "failed_chunks":    job.failed_chunks or 0,
            "progress_pct":     compute_progress_pct(completed, total),
            "started_at":       job.started_at,
            "completed_at":     job.completed_at,
            "created_at":       job.created_at,
        })
    return result


@router.get("/{job_id}", summary="Get full job detail")
def get_job(job_id: str, db: Session = Depends(get_db)):
    """
    Full detail for a single job including computed progress and ETA.

    Response:
    {
      "job_id": "abc-123",
      "status": "running",
      "progress_pct": 56.2,
      "rows_migrated": 2810000,
      "rows_total": 5000000,
      "throughput_rps": 45000,
      "throughput_human": "45,000 rows/sec",
      "eta_seconds": 48,
      "eta_human": "48s",
      "elapsed_human": "1m 3s",
      ...
    }
    """
    job = repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return build_job_detail(db, job)


@router.get("/{job_id}/tables", summary="Get per-table progress breakdown")
def get_job_tables(job_id: str, db: Session = Depends(get_db)):
    """
    Per-table progress for a job.

    Response:
    [
      {
        "table_name": "users",
        "status": "completed",
        "completion_pct": 100.0,
        "total_chunks": 50,
        "completed_chunks": 50
      },
      {
        "table_name": "orders",
        "status": "running",
        "completion_pct": 44.0,
        "total_chunks": 100,
        "completed_chunks": 44
      }
    ]
    """
    job = repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    tables = repo.get_tables_for_job(db, job_id)
    result = []
    for table in tables:
        total     = table.total_chunks or 0
        completed = table.completed_chunks or 0
        pct       = compute_progress_pct(completed, total)

        result.append({
            "table_id":         str(table.id),
            "table_name":       table.table_name,
            "status":           table.status,
            "completion_pct":   pct,
            "total_rows":       table.total_rows or 0,
            "total_chunks":     total,
            "completed_chunks": completed,
            "failed_chunks":    table.failed_chunks or 0,
        })
    return result
