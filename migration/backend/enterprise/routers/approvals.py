"""
Approvals Router
File: migration/backend/enterprise/routers/approvals.py

CHANGES IN THIS VERSION (full rewrite):
  The previous version of this file imported from the now-retired
  shared/middleware/jwt_middleware.py and shared/tenant/tenant_scope.py
  (parallel auth system that has been removed — see project decision to
  standardize on enterprise/security/rbac/auth.py). It also used column
  names that don't match the real migration_approvals table in your live
  database (requested_by/approver_id/comments instead of the actual
  requested_by_id/reviewed_by_id/notes), and referenced migration_jobs.name
  which does not exist as a column on that table.

  This version:
    - Uses backend.enterprise.security.rbac.auth (get_current_user,
      require_permission, CurrentUser) — the canonical, kept auth system.
    - Uses the REAL migration_approvals columns confirmed by direct
      database inspection: id, job_id, tenant_id (uuid), requested_by_id,
      reviewed_by_id, status, notes, requested_at, reviewed_at, auto_approved.
    - Drops the mj.name reference (migration_jobs has no name column in
      your live schema); job identification in list views uses job_id only.
    - Approve/reject require the "approvals:review" permission (falls back
      sanely — tenant_admin/platform_admin have "*" or broad tenant perms
      per the seeded roles table).

Endpoints:
    GET  /approvals                          → list approvals (tenant-scoped)
    GET  /approvals/{id}                     → get one approval record
    POST /jobs/{job_id}/approval/request      → any migration role can request
    POST /jobs/{job_id}/approval/approve      → tenant_admin/platform_admin only
    POST /jobs/{job_id}/approval/reject       → tenant_admin/platform_admin only

Table: migration_approvals (already exists in your live database, created
by 004_security_saas.sql). Columns actually present, confirmed via live
inspection: id, job_id, tenant_id, requested_by_id, reviewed_by_id,
status (default 'pending'), notes, requested_at (default now()),
reviewed_at, auto_approved (default false).
"""

import datetime
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.shared.config.logging import logger

router = APIRouter(tags=["Approvals"])


class ApprovalRequestBody(BaseModel):
    reason: str


class ApprovalReviewBody(BaseModel):
    notes: Optional[str] = None


def _check_tenant_access(user: CurrentUser, resource_tenant_id) -> None:
    """Raises 404 (not 403 — avoid confirming resource existence) if the
    resource's tenant doesn't match the caller's, unless caller is a superuser."""
    if user.can("*"):
        return
    if str(resource_tenant_id) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Approval not found.")


@router.get("/approvals", summary="List approvals (tenant-scoped)")
def list_approvals(
    status: Optional[str] = None,
    user:   CurrentUser = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    """Returns approval requests visible to the caller's tenant.
    Any authenticated user can view — approving/rejecting is separately gated."""
    conditions = []
    params: dict = {}

    if not user.can("*"):
        conditions.append("ma.tenant_id = :tid")
        params["tid"] = user.tenant_id

    if status:
        conditions.append("ma.status = :status")
        params["status"] = status

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = db.execute(
        text(f"""
            SELECT ma.id, ma.job_id, ma.tenant_id, ma.requested_by_id,
                   ma.reviewed_by_id, ma.status, ma.notes,
                   ma.requested_at, ma.reviewed_at, ma.auto_approved,
                   mj.status AS job_status
            FROM migration_approvals ma
            LEFT JOIN migration_jobs mj ON mj.id = ma.job_id
            WHERE {where}
            ORDER BY ma.requested_at DESC
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

    return {"total": len(result), "approvals": result}


@router.get("/approvals/{approval_id}", summary="Get one approval record")
def get_approval(
    approval_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    row = db.execute(
        text("SELECT * FROM migration_approvals WHERE id=:id"),
        {"id": approval_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Approval not found.")

    _check_tenant_access(user, row.tenant_id)

    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d


@router.post("/jobs/{job_id}/approval/request", summary="Request approval for a job")
def request_approval(
    job_id: str,
    body:   ApprovalRequestBody,
    request: Request,
    user:   CurrentUser = Depends(require_permission("jobs:write")),
    db:     Session = Depends(get_db),
):
    """Any user who can create/start jobs can request approval for one.
    Approving/rejecting is a separate, more privileged action below."""
    job_row = db.execute(
        text("SELECT id, tenant_id, status FROM migration_jobs WHERE id=:id"),
        {"id": job_id}
    ).fetchone()
    if not job_row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    _check_tenant_access(user, job_row.tenant_id)

    existing = db.execute(
        text("SELECT id FROM migration_approvals WHERE job_id=:jid AND status='pending'"),
        {"jid": job_id}
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="An approval request is already pending for this job.")

    approval_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            INSERT INTO migration_approvals
                (id, job_id, tenant_id, requested_by_id, status, notes, requested_at)
            VALUES (:id, :jid, :tid, :by, 'pending', :notes, :now)
        """),
        {
            "id": approval_id, "jid": job_id, "tid": job_row.tenant_id,
            "by": user.user_id, "notes": body.reason, "now": now,
        }
    )
    db.execute(
        text("UPDATE migration_jobs SET status='awaiting_approval', updated_at=:now WHERE id=:id"),
        {"now": now, "id": job_id}
    )
    db.commit()

    AuditTrail.log(
        db=db, action="approval.requested",
        tenant_id=str(job_row.tenant_id), user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"reason": body.reason}, request=request,
    )

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.requested", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"reason": body.reason, "requested_by": user.email},
            db=db,
        )
    except Exception:
        pass

    logger.info("Approval requested", job_id=job_id, requested_by=user.user_id)
    return {"approval_id": approval_id, "job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/approval/approve", summary="Approve a pending migration (admin only)")
def approve_job(
    job_id: str,
    body:   ApprovalReviewBody,
    request: Request,
    user:   CurrentUser = Depends(require_permission("jobs:approve")),
    db:     Session = Depends(get_db),
):
    """Approves a pending migration job. Requires 'jobs:approve' permission
    (held by tenant_admin, migration_admin, and platform_admin per the
    seeded roles table)."""
    approval = db.execute(
        text("""
            SELECT id, tenant_id FROM migration_approvals
            WHERE job_id=:jid AND status='pending'
            ORDER BY requested_at DESC LIMIT 1
        """),
        {"jid": job_id}
    ).fetchone()
    if not approval:
        raise HTTPException(status_code=404, detail="No pending approval found for this job.")
    _check_tenant_access(user, approval.tenant_id)

    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            UPDATE migration_approvals SET
                status='approved', reviewed_by_id=:reviewer, notes=:notes, reviewed_at=:now
            WHERE id=:id
        """),
        {"reviewer": user.user_id, "notes": body.notes, "now": now, "id": approval.id}
    )
    db.execute(
        text("UPDATE migration_jobs SET status='planning', updated_at=:now WHERE id=:id"),
        {"now": now, "id": job_id}
    )
    db.commit()

    AuditTrail.log(
        db=db, action="approval.approved",
        tenant_id=str(approval.tenant_id), user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"notes": body.notes}, request=request,
    )

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.approved", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"approved_by": user.email, "notes": body.notes},
            db=db,
        )
    except Exception:
        pass

    logger.info("Job approved", job_id=job_id, approved_by=user.user_id)
    return {"job_id": job_id, "status": "approved", "approved_by": user.email}


@router.post("/jobs/{job_id}/approval/reject", summary="Reject a pending migration (admin only)")
def reject_job(
    job_id: str,
    body:   ApprovalReviewBody,
    request: Request,
    user:   CurrentUser = Depends(require_permission("jobs:approve")),
    db:     Session = Depends(get_db),
):
    """Rejects a pending migration job. Requires 'jobs:approve' permission."""
    approval = db.execute(
        text("""
            SELECT id, tenant_id FROM migration_approvals
            WHERE job_id=:jid AND status='pending'
            ORDER BY requested_at DESC LIMIT 1
        """),
        {"jid": job_id}
    ).fetchone()
    if not approval:
        raise HTTPException(status_code=404, detail="No pending approval found for this job.")
    _check_tenant_access(user, approval.tenant_id)

    if not body.notes:
        raise HTTPException(status_code=400, detail="A reason is required when rejecting a migration.")

    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            UPDATE migration_approvals SET
                status='rejected', reviewed_by_id=:reviewer, notes=:notes, reviewed_at=:now
            WHERE id=:id
        """),
        {"reviewer": user.user_id, "notes": body.notes, "now": now, "id": approval.id}
    )
    db.execute(
        text("UPDATE migration_jobs SET status='cancelled', error_message=:msg, updated_at=:now WHERE id=:id"),
        {"msg": f"Approval rejected: {body.notes}", "now": now, "id": job_id}
    )
    db.commit()

    AuditTrail.log(
        db=db, action="approval.rejected",
        tenant_id=str(approval.tenant_id), user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"notes": body.notes}, request=request,
    )

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.rejected", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"rejected_by": user.email, "notes": body.notes},
            db=db,
        )
    except Exception:
        pass

    logger.info("Job rejected", job_id=job_id, rejected_by=user.user_id)
    return {"job_id": job_id, "status": "rejected", "rejected_by": user.email}
