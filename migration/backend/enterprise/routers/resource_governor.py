"""
Resource Governor Router
File: migration/backend/enterprise/routers/resource_governor.py

Endpoints:
    POST /jobs/{id}/governor/start    → start resource monitoring for a job
    POST /jobs/{id}/governor/stop     → stop monitoring and clear throttle
    GET  /jobs/{id}/governor/status   → current throttle state and metrics
    GET  /jobs/{id}/governor/history  → historical resource readings
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict

from backend.shared.config.database import get_db
from backend.enterprise.resource_governor.governor import ResourceGovernor
from backend.enterprise.connection_manager.connection_manager import ConnectionManager
from backend.shared.config.redis import redis_client

router = APIRouter(prefix="/jobs", tags=["Resource Governor"])
cm     = ConnectionManager()

# In-memory governor registry (per process)
# In production this would be managed differently with Kubernetes
_governors: Dict[str, ResourceGovernor] = {}


class GovernorStartRequest(BaseModel):
    source_connection_id: Optional[str] = None
    target_connection_id: Optional[str] = None
    source_config:        Optional[Dict] = None
    target_config:        Optional[Dict] = None
    max_workers:          int = 4


@router.post("/{job_id}/governor/start", summary="Start resource monitoring for a job")
def start_governor(job_id: str, req: GovernorStartRequest, db: Session = Depends(get_db)):
    """
    Starts a background thread that monitors source/target DB load every 30 seconds.

    When resource pressure is detected (CPU >85%, connections >85% of max):
      - Sets a Redis throttle key: migration:throttle:{job_id}
      - Workers check this key before pulling new chunks
      - Reduces allowed worker count to max_workers/2

    When pressure recovers:
      - Throttle key is automatically removed
      - Workers resume at full capacity

    All throttle events are recorded to resource_governor_state table.
    """
    if job_id in _governors and _governors[job_id].running:
        return {"status": "already_running", "job_id": job_id}

    # Resolve configs
    source_config = req.source_config
    target_config = req.target_config

    if req.source_connection_id and not source_config:
        source_config = cm.get_config(db, req.source_connection_id)
        if not source_config:
            raise HTTPException(status_code=404, detail="Source connection not found")

    if req.target_connection_id and not target_config:
        target_config = cm.get_config(db, req.target_connection_id)
        if not target_config:
            raise HTTPException(status_code=404, detail="Target connection not found")

    if not source_config:
        raise HTTPException(status_code=400, detail="Provide source_connection_id or source_config")
    if not target_config:
        raise HTTPException(status_code=400, detail="Provide target_connection_id or target_config")

    governor = ResourceGovernor(
        job_id=job_id,
        source_config=source_config,
        target_config=target_config,
        max_workers=req.max_workers,
    )
    governor.start()
    _governors[job_id] = governor

    return {
        "status":      "started",
        "job_id":      job_id,
        "max_workers": req.max_workers,
        "check_interval_sec": 30,
        "thresholds": {
            "source_conn_pct_throttle":  85,
            "worker_memory_pct_throttle": 90,
        }
    }


@router.post("/{job_id}/governor/stop", summary="Stop resource monitoring")
def stop_governor(job_id: str):
    """Stops the resource governor and clears any active throttle state."""
    if job_id in _governors:
        _governors[job_id].stop()
        del _governors[job_id]
        return {"status": "stopped", "job_id": job_id}
    return {"status": "was_not_running", "job_id": job_id}


@router.get("/{job_id}/governor/status", summary="Get current throttle state")
def get_governor_status(job_id: str):
    """
    Returns the current throttle state for a job.

    Response:
    {
      "job_id": "...",
      "governor_running": true,
      "throttle_active": false,
      "allowed_workers": 4,
      "throttle_reason": null
    }
    """
    governor = _governors.get(job_id)
    throttle_key = f"migration:throttle:{job_id}"
    throttle_val = redis_client.get(throttle_key)

    allowed_workers = int(throttle_val) if throttle_val else (
        governor.max_workers if governor else 4
    )

    return {
        "job_id":           job_id,
        "governor_running": governor is not None and governor.running,
        "throttle_active":  throttle_val is not None,
        "allowed_workers":  allowed_workers,
    }


@router.get("/{job_id}/governor/history", summary="Get resource monitoring history")
def get_governor_history(job_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """
    Returns the last N resource governor readings for a job.
    Useful for debugging throttle events and understanding DB load patterns.
    """
    rows = db.execute(
        text("""
            SELECT recorded_at, source_db_conn_count, target_db_conn_count,
                   worker_count_active, redis_queue_depth, rows_per_sec,
                   throttle_applied, throttle_reason
            FROM resource_governor_state
            WHERE job_id = :jid
            ORDER BY recorded_at DESC
            LIMIT :lim
        """),
        {"jid": job_id, "lim": limit}
    ).fetchall()

    readings = []
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("recorded_at"), "isoformat"):
            d["recorded_at"] = d["recorded_at"].isoformat()
        readings.append(d)

    throttle_events = sum(1 for r in readings if r.get("throttle_applied"))

    return {
        "job_id":          job_id,
        "total_readings":  len(readings),
        "throttle_events": throttle_events,
        "readings":        readings,
    }
