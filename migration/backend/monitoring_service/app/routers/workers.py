"""
Workers Router
File: migration/backend/monitoring_service/app/routers/workers.py

Endpoints:
    GET /workers    → all workers with current status and chunk
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.monitoring_service.app.repositories.monitoring_repository import MonitoringRepository

router = APIRouter(prefix="/workers", tags=["Workers"])
repo   = MonitoringRepository()


@router.get("", summary="List all workers and their current status")
def get_workers(db: Session = Depends(get_db)):
    """
    Returns all registered workers with their live status.

    is_stale=true means the worker's last heartbeat is >15 minutes old —
    the worker process has likely crashed.

    Response:
    [
      {
        "worker_name": "worker-a3f9b2c1",
        "worker_status": "BUSY",
        "current_chunk_id": "chunk-uuid-here",
        "hostname": "prod-server-01",
        "cpu_usage": 45.2,
        "memory_usage": 62.1,
        "last_heartbeat": "2026-06-24T10:30:00",
        "is_stale": false
      },
      {
        "worker_name": "worker-d8e2f1b4",
        "worker_status": "IDLE",
        "current_chunk_id": null,
        "hostname": "prod-server-01",
        "cpu_usage": 5.1,
        "memory_usage": 58.3,
        "last_heartbeat": "2026-06-24T10:30:02",
        "is_stale": false
      }
    ]
    """
    return repo.get_all_workers(db)
