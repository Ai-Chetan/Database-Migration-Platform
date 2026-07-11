"""
Operations Console Router
File: migration/backend/operations/routers/operations.py

All Operations Console endpoints. The Kubernetes Dashboard equivalent
for the Migration Platform — gives operators manual control over every
aspect of a running migration.

── WORKER CONTROL ────────────────────────────────────────────────────────────
    GET  /ops/workers                          List all workers
    GET  /ops/workers/{job_id}                 List workers for a job
    POST /ops/workers/{id}/pause               Pause after current chunk
    POST /ops/workers/{id}/resume              Resume pulling chunks
    POST /ops/workers/{id}/kill                Stop immediately
    POST /ops/workers/{id}/quarantine          Pause + flag for investigation
    POST /ops/jobs/{id}/workers/scale          Set target worker count
    POST /ops/jobs/{id}/workers/drain          Drain all workers for a job

── CHUNK CONTROL ─────────────────────────────────────────────────────────────
    GET  /ops/jobs/{id}/chunks/problems        Chunks needing attention
    GET  /ops/chunks/{id}                      Full chunk detail
    POST /ops/chunks/{id}/reassign             Move to different worker/queue
    POST /ops/chunks/{id}/retry                Reset failed chunk for retry
    POST /ops/chunks/{id}/skip                 Skip chunk (operator must supply reason)

── JOB CONTROL ───────────────────────────────────────────────────────────────
    GET  /ops/jobs/{id}/live-stats             Real-time job stats + ETA
    POST /ops/jobs/{id}/pause                  Pause job gracefully
    POST /ops/jobs/{id}/resume                 Resume paused job
    POST /ops/jobs/{id}/cancel                 Cancel job permanently
    POST /ops/jobs/{id}/rerun-validation       Re-run post-migration validation

── MAINTENANCE MODE ──────────────────────────────────────────────────────────
    GET  /ops/maintenance                      Get maintenance mode status
    POST /ops/maintenance/enable               Enable maintenance mode
    POST /ops/maintenance/disable              Disable maintenance mode
    POST /ops/maintenance/emergency-stop       EMERGENCY: halt everything

── AUDIT LOG ─────────────────────────────────────────────────────────────────
    GET  /ops/actions                          List all operator actions
    GET  /ops/actions/{resource_id}            Actions for one resource
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.operations.worker_control.worker_control import WorkerControl
from backend.operations.chunk_control.chunk_control import ChunkControl
from backend.operations.job_control.job_control import JobControl

router        = APIRouter(prefix="/ops", tags=["Operations Console"])
worker_ctrl   = WorkerControl()
chunk_ctrl    = ChunkControl()
job_ctrl      = JobControl()


# ── Request models ─────────────────────────────────────────────────────────────

class ActionRequest(BaseModel):
    reason:    str = ""
    operator:  str = "operator"
    tenant_id: str = "local"

class ScaleRequest(BaseModel):
    target_count: int
    reason:       str = ""
    operator:     str = "operator"
    tenant_id:    str = "local"

class ReassignRequest(BaseModel):
    target_worker: Optional[str] = None
    reason:        str = ""
    operator:      str = "operator"
    tenant_id:     str = "local"

class MaintenanceRequest(BaseModel):
    reason:    str
    operator:  str = "operator"
    tenant_id: str = "local"

class ValidationRequest(BaseModel):
    table_name: Optional[str] = None
    operator:   str = "operator"
    tenant_id:  str = "local"


# ── Worker Control endpoints ───────────────────────────────────────────────────

@router.get("/workers", summary="List all active workers")
def list_all_workers(db: Session = Depends(get_db)):
    """
    Returns all workers with current status, job, chunk, and last heartbeat.
    Stale workers (no heartbeat >2 min) are flagged as potentially offline.
    """
    workers = worker_ctrl.list_workers(db)
    online  = sum(1 for w in workers if w.get("status") in ("BUSY", "IDLE"))
    busy    = sum(1 for w in workers if w.get("status") == "BUSY")
    return {
        "total":   len(workers),
        "online":  online,
        "busy":    busy,
        "idle":    online - busy,
        "workers": workers,
    }


@router.get("/workers/{job_id}/job", summary="List workers for a specific job")
def list_job_workers(job_id: str, db: Session = Depends(get_db)):
    workers = worker_ctrl.list_workers(db, job_id=job_id)
    return {"job_id": job_id, "worker_count": len(workers), "workers": workers}


@router.post("/workers/{worker_id}/pause", summary="Pause a worker after current chunk")
def pause_worker(worker_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    return worker_ctrl.pause_worker(db, worker_id, req.reason, req.operator, req.tenant_id)


@router.post("/workers/{worker_id}/resume", summary="Resume a paused worker")
def resume_worker(worker_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    return worker_ctrl.resume_worker(db, worker_id, req.reason, req.operator, req.tenant_id)


@router.post("/workers/{worker_id}/kill", summary="Kill a worker immediately")
def kill_worker(worker_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """
    Sends an immediate stop signal. In-progress chunk will be abandoned
    and retried by stale chunk recovery. Use when a worker is stuck.
    """
    if not req.reason:
        raise HTTPException(status_code=400, detail="reason is required when killing a worker")
    return worker_ctrl.kill_worker(db, worker_id, req.reason, req.operator, req.tenant_id)


@router.post("/workers/{worker_id}/quarantine", summary="Quarantine a worker for investigation")
def quarantine_worker(worker_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """Pauses the worker AND flags it as quarantined for operator investigation."""
    if not req.reason:
        raise HTTPException(status_code=400, detail="reason is required when quarantining a worker")
    return worker_ctrl.quarantine_worker(db, worker_id, req.reason, req.operator, req.tenant_id)


@router.post("/jobs/{job_id}/workers/scale", summary="Set target worker count for a job")
def scale_workers(job_id: str, req: ScaleRequest, db: Session = Depends(get_db)):
    """
    Override the target worker count for a running job.
    Workers themselves must be started/stopped manually — this sets the
    desired count that the Self-Tuning Engine and resource governor respect.
    """
    return worker_ctrl.scale_workers(
        db, job_id, req.target_count, req.reason, req.operator, req.tenant_id
    )


@router.post("/jobs/{job_id}/workers/drain", summary="Gracefully drain all workers for a job")
def drain_workers(job_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """Signal all workers for this job to finish current chunk then stop."""
    return worker_ctrl.drain_all_workers(db, job_id, req.reason, req.operator, req.tenant_id)


# ── Chunk Control endpoints ────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/chunks/problems",
            summary="List chunks needing operator attention")
def list_problem_chunks(job_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """
    Returns failed chunks, stale running chunks, and high-retry chunks.
    This is the primary Operations Console view for chunk-level triage.
    """
    problems = chunk_ctrl.list_problem_chunks(db, job_id, limit)
    return {
        "job_id":  job_id,
        "total":   len(problems),
        "failed":  sum(1 for p in problems if p.get("problem_type") == "failed"),
        "stale":   sum(1 for p in problems if p.get("problem_type") == "stale"),
        "high_retries": sum(1 for p in problems if p.get("problem_type") == "high_retries"),
        "chunks":  problems,
    }


@router.get("/chunks/{chunk_id}", summary="Get full detail for one chunk")
def get_chunk_detail(chunk_id: str, db: Session = Depends(get_db)):
    detail = chunk_ctrl.get_chunk_detail(db, chunk_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")
    return detail


@router.post("/chunks/{chunk_id}/reassign", summary="Reassign chunk to different worker or queue")
def reassign_chunk(chunk_id: str, req: ReassignRequest, db: Session = Depends(get_db)):
    """
    Move a stuck chunk to a different worker, or return it to the queue.
    target_worker=null → return to queue for any available worker.
    target_worker="worker-id" → assign directly to that worker.
    """
    return chunk_ctrl.reassign_chunk(
        db, chunk_id, req.target_worker, req.reason, req.operator, req.tenant_id
    )


@router.post("/chunks/{chunk_id}/retry", summary="Force retry a failed chunk")
def retry_chunk(chunk_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """Reset a failed chunk to pending for retry, bypassing the retry count limit."""
    return chunk_ctrl.retry_chunk(db, chunk_id, req.reason, req.operator, req.tenant_id)


@router.post("/chunks/{chunk_id}/skip", summary="Skip a chunk (data will not be migrated)")
def skip_chunk(chunk_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """
    Mark a chunk as skipped. Data in this PK range will NOT be migrated.
    Requires a reason. Use when data is known-bad or handled separately.
    """
    if not req.reason:
        raise HTTPException(status_code=400,
                            detail="reason is required when skipping a chunk")
    return chunk_ctrl.skip_chunk(db, chunk_id, req.reason, req.operator, req.tenant_id)


# ── Job Control endpoints ──────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/live-stats", summary="Real-time job statistics")
def get_live_stats(job_id: str, db: Session = Depends(get_db)):
    """
    Returns live progress, throughput (rows/sec), ETA, active workers,
    error rate, and chunk breakdown — everything the Job Monitor UI needs.
    """
    stats = job_ctrl.get_live_stats(db, job_id)
    if not stats:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return stats


@router.post("/jobs/{job_id}/pause", summary="Pause a running job")
def pause_job(job_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    return job_ctrl.pause_job(db, job_id, req.reason, req.operator, req.tenant_id)


@router.post("/jobs/{job_id}/resume", summary="Resume a paused job")
def resume_job(job_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    return job_ctrl.resume_job(db, job_id, req.reason, req.operator, req.tenant_id)


@router.post("/jobs/{job_id}/cancel", summary="Cancel a job permanently")
def cancel_job(job_id: str, req: ActionRequest, db: Session = Depends(get_db)):
    """
    Permanently cancels a job. Cannot be undone.
    Workers drain gracefully. Use rollback if target data needs cleanup.
    """
    if not req.reason:
        raise HTTPException(status_code=400,
                            detail="reason is required when cancelling a job")
    return job_ctrl.cancel_job(db, job_id, req.reason, req.operator, req.tenant_id)


@router.post("/jobs/{job_id}/rerun-validation",
             summary="Re-run post-migration validation")
def rerun_validation(job_id: str, req: ValidationRequest, db: Session = Depends(get_db)):
    """
    Resets validation status on completed chunks, triggering re-verification
    by the next available worker. Optionally filter to one table.
    """
    return job_ctrl.rerun_validation(
        db, job_id, req.table_name, req.operator, req.tenant_id
    )


# ── Maintenance Mode endpoints ─────────────────────────────────────────────────

@router.get("/maintenance", summary="Get maintenance mode status")
def get_maintenance(tenant_id: str = "local", db: Session = Depends(get_db)):
    return job_ctrl.get_maintenance_status(db, tenant_id)


@router.post("/maintenance/enable", summary="Enable maintenance mode")
def enable_maintenance(req: MaintenanceRequest, db: Session = Depends(get_db)):
    """
    Blocks new job starts. Workers finish current chunks then stop.
    Required before platform upgrades or infrastructure maintenance.
    """
    return job_ctrl.enable_maintenance(db, req.reason, req.operator, req.tenant_id)


@router.post("/maintenance/disable", summary="Disable maintenance mode")
def disable_maintenance(req: ActionRequest, db: Session = Depends(get_db)):
    return job_ctrl.disable_maintenance(db, req.operator, req.tenant_id)


@router.post("/maintenance/emergency-stop",
             summary="EMERGENCY: Halt all jobs and workers immediately")
def emergency_stop(req: MaintenanceRequest, db: Session = Depends(get_db)):
    """
    EMERGENCY USE ONLY. Kills all workers immediately and pauses all jobs.
    Enables maintenance mode. Requires manual intervention to resume.
    """
    if not req.reason:
        raise HTTPException(status_code=400,
                            detail="reason is required for emergency stop")
    return job_ctrl.emergency_stop(db, req.reason, req.operator, req.tenant_id)


# ── Audit Log endpoints ────────────────────────────────────────────────────────

@router.get("/actions", summary="List all operator actions")
def list_actions(
    tenant_id:     str = "local",
    resource_type: Optional[str] = None,
    action_type:   Optional[str] = None,
    limit:         int = 100,
    db:            Session = Depends(get_db),
):
    """Returns the full audit log of operator actions via the Operations Console."""
    conditions = ["tenant_id=:tid"]
    params: Dict[str, Any] = {"tid": tenant_id, "lim": limit}

    if resource_type:
        conditions.append("resource_type=:rtype")
        params["rtype"] = resource_type
    if action_type:
        conditions.append("action_type=:atype")
        params["atype"] = action_type

    rows = db.execute(
        text(f"""
            SELECT id, operator_id, action_type, resource_type, resource_id,
                   before_state, after_state, reason, status, created_at
            FROM operations_actions
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC LIMIT :lim
        """),
        params
    ).fetchall()

    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return {"total": len(result), "actions": result}


@router.get("/actions/{resource_id}", summary="Get operator actions for one resource")
def get_resource_actions(resource_id: str, limit: int = 50,
                         db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, operator_id, action_type, resource_type,
                   reason, status, created_at
            FROM operations_actions
            WHERE resource_id=:rid
            ORDER BY created_at DESC LIMIT :lim
        """),
        {"rid": resource_id, "lim": limit}
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return {"resource_id": resource_id, "total": len(result), "actions": result}
