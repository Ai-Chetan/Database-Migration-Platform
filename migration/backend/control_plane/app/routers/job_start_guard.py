"""
Job Start Guard
File: migration/backend/control_plane/app/routers/job_start_guard.py

Wires the Policy Engine (Part 8) into the actual job-start flow.
Previously, POST /plugins/policies/check/{job_id} existed as a standalone
diagnostic endpoint but nothing called it automatically before a job
was allowed to run - an operator could bypass all configured policies
(require_approval, forbidden_lossy_conversion, require_masking_for_pii, etc.)
simply by not calling that endpoint first.

This module provides a single function, enforce_policies_or_raise(),
that the existing POST /jobs/{id}/start handler must call as its first
action. It is intentionally a small, standalone function (not a new router)
so it can be dropped into your existing control_plane job start handler
with a two-line change rather than a rewrite.

USAGE - add these two lines to your existing job start handler in
migration/backend/control_plane/app/routers/jobs.py:

    from backend.control_plane.app.routers.job_start_guard import enforce_policies_or_raise

    @router.post("/jobs/{job_id}/start")
    def start_job(job_id: str, user: dict = Depends(...), db: Session = Depends(get_db)):
        enforce_policies_or_raise(db, job_id, user)   # <-- ADD THIS LINE FIRST
        # ... existing start logic continues unchanged below ...
"""

from typing import Dict, Any, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


def enforce_policies_or_raise(
    db:      Session,
    job_id:  str,
    user:    Dict[str, Any],
    dry_run_result: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Runs all active organizational policies for the job's tenant.
    Raises HTTPException(423, ...) if any BLOCKING policy fails.
    Raises HTTPException(403, ...) if the job requires approval and
    is not yet approved.

    Call this as the FIRST line inside POST /jobs/{id}/start.
    """
    from backend.plugins.policy.policy_plugins import PolicyRunner

    job_row = db.execute(
        text("""
            SELECT id, tenant_id, status, mapping_project_id
            FROM migration_jobs WHERE id=:id
        """),
        {"id": job_id}
    ).fetchone()

    if not job_row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    if job_row.tenant_id != user.get("tenant_id") and user.get("role") != "platform_admin":
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    if job_row.status == "awaiting_approval":
        raise HTTPException(
            status_code=403,
            detail="This job is awaiting approval and cannot be started until "
                   "approved by a tenant administrator."
        )

    approval_status = _get_latest_approval_status(db, job_id)

    if dry_run_result is None and job_row.mapping_project_id:
        dry_run_result = _load_latest_dry_run(db, job_row.mapping_project_id)

    result = PolicyRunner.check_all(
        db=db,
        job_id=job_id,
        tenant_id=job_row.tenant_id,
        dry_run_result=dry_run_result,
        approval_status=approval_status,
        context={},
    )

    if not result.get("can_proceed", True):
        violations = result.get("violations", [])
        messages = "; ".join(v.get("message", "") for v in violations[:3])
        logger.warning("Job start blocked by policy", job_id=job_id, violations=len(violations))
        raise HTTPException(
            status_code=423,
            detail=f"Cannot start migration: {len(violations)} policy violation(s). {messages}"
        )

    if result.get("warnings"):
        logger.info("Job starting with policy warnings", job_id=job_id,
                    warning_count=len(result["warnings"]))


def _get_latest_approval_status(db: Session, job_id: str) -> Optional[str]:
    row = db.execute(
        text("""
            SELECT status FROM migration_approvals
            WHERE job_id=:jid ORDER BY requested_at DESC LIMIT 1
        """),
        {"jid": job_id}
    ).fetchone()
    return row[0] if row else None


def _load_latest_dry_run(db: Session, mapping_project_id: str) -> Optional[Dict[str, Any]]:
    try:
        row = db.execute(
            text("""
                SELECT unsafe_conversions, lossy_conversions, warnings
                FROM schema_dry_run_results
                WHERE project_id=:pid ORDER BY created_at DESC LIMIT 1
            """),
            {"pid": mapping_project_id}
        ).fetchone()
        if not row:
            return None
        return {
            "unsafe_conversions": row[0] or [],
            "lossy_conversions":  row[1] or [],
            "warnings":           row[2] or [],
        }
    except Exception:
        return None
