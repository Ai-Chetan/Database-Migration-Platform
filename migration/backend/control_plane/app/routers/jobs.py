"""
Jobs Router
File: migration/backend/control_plane/app/routers/jobs.py

NEW FILE — this was the direct cause of the startup warning:
    [WARN] Control Plane routers not loaded: cannot import name 'jobs'
    from 'backend.control_plane.app.routers'
main.py expected this module to exist and export a `router` object with
job CRUD + lifecycle endpoints. It didn't exist at all — the underlying
orchestration logic (JobManager, MigrationJobRepository, Planner) was
already fully built, just with no HTTP layer on top. This file is that
HTTP layer.

Endpoints:
    POST   /jobs                → create a new migration job
    GET    /jobs                → list jobs (tenant-scoped, filterable by status)
    GET    /jobs/{id}           → get one job
    POST   /jobs/{id}/start     → start a job (runs policy check, then transitions to planning)
    POST   /jobs/{id}/pause     → pause a running job
    POST   /jobs/{id}/resume    → resume a paused job
    POST   /jobs/{id}/cancel    → cancel a job
    DELETE /jobs/{id}           → delete a job record entirely

Wired to the real, existing orchestration layer:
    JobManager                → orchestrator/job_manager.py (lifecycle transitions)
    MigrationJobRepository    → repositories/migration_job_repository.py (persistence)
    enforce_policies_or_raise → job_start_guard.py (blocks start on policy violations)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.shared.exceptions.base import PlatformException
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.control_plane.app.orchestrator.job_manager import JobManager
from backend.control_plane.app.repositories.migration_job_repository import MigrationJobRepository
from backend.control_plane.app.routers.job_start_guard import enforce_policies_or_raise
from backend.shared.config.logging import logger

router = APIRouter(prefix="/jobs", tags=["Migration Jobs"])

job_manager = JobManager()
job_repo    = MigrationJobRepository()


class CreateJobRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]


def _job_to_dict(job) -> dict:
    return {
        "id":               str(job.id),
        "tenant_id":        job.tenant_id,
        "status":           job.status,
        "source_config":    job.source_config,
        "target_config":    job.target_config,
        "total_tables":     job.total_tables,
        "total_chunks":     job.total_chunks,
        "completed_chunks": job.completed_chunks,
        "failed_chunks":    job.failed_chunks,
        "created_at":       job.created_at.isoformat() if job.created_at else None,
        "started_at":       job.started_at.isoformat() if job.started_at else None,
        "completed_at":     job.completed_at.isoformat() if job.completed_at else None,
        "last_error":       job.last_error,
    }


@router.post("", summary="Create a new migration job")
def create_job(
    req:  CreateJobRequest,
    user: CurrentUser = Depends(require_permission("jobs:create")),
    db:   Session = Depends(get_db),
):
    """
    Creates a job in 'pending' status. Call POST /jobs/{id}/start to begin
    execution once you're ready (after schema mapping, dry-run, and
    simulation are complete — see the New Migration wizard flow).

    source_config / target_config shape:
    {
      "engine": "mysql", "host": "...", "port": 3306,
      "database": "...", "username": "...", "password": "..."
    }
    """
    job = job_manager.create_job(
        db=db,
        source_config=req.source_config,
        target_config=req.target_config,
        tenant_id=user.tenant_id,
    )
    logger.info("Job created", job_id=str(job.id), created_by=user.user_id)
    return _job_to_dict(job)


@router.get("", summary="List migration jobs")
def list_jobs(
    status: Optional[str] = None,
    limit:  int = 50,
    offset: int = 0,
    user:   CurrentUser = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    """Lists jobs for the caller's tenant. platform_admin sees all tenants."""
    tenant_filter = None if user.can("*") else user.tenant_id
    jobs = job_repo.list_jobs(db, tenant_id=tenant_filter, status=status, limit=limit, offset=offset)
    return {"total": len(jobs), "jobs": [_job_to_dict(j) for j in jobs]}


@router.get("/{job_id}", summary="Get one job")
def get_job(
    job_id: str,
    user:   CurrentUser = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_dict(job)


@router.post("/{job_id}/start", summary="Start a migration job")
def start_job(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:start")),
    db:     Session = Depends(get_db),
):
    """
    Starts the job. Runs all active organizational policies first
    (require_approval, forbidden_lossy_conversion, require_masking_for_pii,
    etc.) and blocks with HTTP 423 if any blocking policy fails, or
    HTTP 403 if the job is awaiting approval. On success, transitions the
    job to 'planning' status.
    """
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")

    enforce_policies_or_raise(db, job_id, {"id": user.user_id, "tenant_id": user.tenant_id, "role": user.role})

    try:
        job_manager.start_job(db, job_id)
    except PlatformException as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)

    updated = job_repo.get_job_by_id(db, job_id)
    logger.info("Job started", job_id=job_id, started_by=user.user_id)
    return _job_to_dict(updated)


@router.post("/{job_id}/pause", summary="Pause a running job")
def pause_job(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:pause")),
    db:     Session = Depends(get_db),
):
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job_manager.pause_job(db, job_id)
    return _job_to_dict(job_repo.get_job_by_id(db, job_id))


@router.post("/{job_id}/resume", summary="Resume a paused job")
def resume_job(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:resume")),
    db:     Session = Depends(get_db),
):
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job_manager.resume_job(db, job_id)
    return _job_to_dict(job_repo.get_job_by_id(db, job_id))


@router.post("/{job_id}/cancel", summary="Cancel a job")
def cancel_job(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:cancel")),
    db:     Session = Depends(get_db),
):
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job_manager.cancel_job(db, job_id)
    return _job_to_dict(job_repo.get_job_by_id(db, job_id))


@router.delete("/{job_id}", summary="Delete a job record")
def delete_job(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:cancel")),
    db:     Session = Depends(get_db),
):
    """Permanently deletes the job record and its chunks/tables (cascade).
    Prefer POST /jobs/{id}/cancel for jobs that are currently running."""
    job = job_repo.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status == "running":
        raise HTTPException(status_code=400,
                            detail="Cannot delete a running job. Cancel it first.")

    job_repo.delete_job(db, job_id)
    return {"message": "Job deleted.", "job_id": job_id}
