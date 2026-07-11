"""
Chunks Router
File: migration/backend/monitoring_service/app/routers/chunks.py

Endpoints:
    GET /jobs/{job_id}/chunks              → all chunks for a job
    GET /jobs/{job_id}/chunks?status=failed → filter by status
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from backend.shared.config.database import get_db
from backend.monitoring_service.app.repositories.monitoring_repository import MonitoringRepository

router = APIRouter(prefix="/jobs", tags=["Chunks"])
repo   = MonitoringRepository()


@router.get("/{job_id}/chunks", summary="List all chunks for a job")
def get_chunks(
    job_id: str,
    status: Optional[str] = Query(None, description="Filter by status: pending, running, completed, failed, retrying"),
    db: Session = Depends(get_db)
):
    """
    Returns all chunks for a job ordered by pk_start.
    Optionally filter by status.

    Response:
    {
      "job_id": "abc-123",
      "total": 500,
      "chunks": [
        {
          "chunk_id": "chunk-001",
          "table_name": "users",
          "status": "completed",
          "pk_start": 1,
          "pk_end": 100000,
          "rows_processed": 100000,
          "worker_id": "worker-a3f9",
          "retry_count": 0,
          "duration_ms": 2341,
          "throughput_rps": 42716.4,
          "validation_status": "passed",
          "checksum": "a3f9b2c1...",
          "last_error": null
        }
      ]
    }
    """
    job = repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    chunks = repo.get_chunks_for_job(db, job_id)

    if status:
        chunks = [c for c in chunks if c.status == status.lower()]

    chunk_list = []
    for chunk in chunks:
        chunk_list.append({
            "chunk_id":         str(chunk.id),
            "table_name":       chunk.table_name,
            "status":           chunk.status,
            "pk_start":         chunk.pk_start,
            "pk_end":           chunk.pk_end,
            "rows_processed":   chunk.rows_processed or 0,
            "worker_id":        chunk.worker_id,
            "retry_count":      chunk.retry_count or 0,
            "duration_ms":      chunk.duration_ms,
            "throughput_rps":   float(chunk.throughput_rows_per_sec) if chunk.throughput_rows_per_sec else None,
            "validation_status": chunk.validation_status,
            "checksum":         chunk.checksum,
            "last_error":       chunk.last_error,
            "started_at":       chunk.started_at,
            "completed_at":     chunk.completed_at,
        })

    return {
        "job_id": job_id,
        "total":  len(chunk_list),
        "chunks": chunk_list,
    }
