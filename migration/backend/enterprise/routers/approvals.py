"""
Approvals Router (RBAC-corrected version)
File: migration/backend/enterprise/routers/approvals.py

Fixes the granularity gap flagged during RBAC rollout: the global route
middleware cannot distinguish /jobs/{id}/approval/request (any migration
role) from /jobs/{id}/approval/approve (admin only) using path-prefix
matching alone, since they share the same prefix. This router enforces
the correct permission INSIDE each handler using Depends(require_permission)
and Depends(require_role), which is the precise, per-action mechanism.

Endpoints:
    GET  /approvals                          -> list pending approvals (tenant-scoped)
    POST /jobs/{job_id}/approval/request      -> any migration role can request
    POST /jobs/{job_id}/approval/approve      -> tenant_admin/platform_admin only
    POST /jobs/{job_id}/approval/reject       -> tenant_admin/platform_admin only
    GET  /approvals/{id}                      -> get one approval record

Table used: migration_approvals (created in earlier security migration -
004_security_saas.sql). Columns assumed: id, job_id, tenant_id, requested_by,
reason, status, approver_id, comments, requested_at, reviewed_at.
"""

import datetime
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from backend.shared.config.database import get_db
from backend.shared.middleware.jwt_middleware import get_current_user, require_permission
from backend.shared.tenant.tenant_scope import tenant_filter, assert_owned_by_tenant, scoped_params
from backend.shared.config.logging import logger

router = APIRouter(tags=["Approvals"])


class ApprovalRequestBody(BaseModel):
    reason: str


class ApprovalReviewBody(BaseModel):
    comments: Optional[str] = None


@router.get("/approvals", summary="List approvals (tenant-scoped)")
def list_approvals(
    status: Optional[str] = None,
    user:   dict = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    where_sql, tparams = tenant_filter(user, table_alias="ma")
    conditions = [where_sql]
    params = scoped_params(user)

    if status:
        conditions.append("ma.status = :status")
        params["status"] = status

    rows = db.execute(
        text(f"""
            SELECT ma.id, ma.job_id, ma.tenant_id, ma.requested_by, ma.reason,
                   ma.status, ma.approver_id, ma.comments,
                   ma.requested_at, ma.reviewed_at,
                   mj.name AS job_name
            FROM migration_approvals ma
            LEFT JOIN migration_jobs mj ON mj.id = ma.job_id
            WHERE {' AND '.join(conditions)}
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
def get_approval(approval_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM migration_approvals WHERE id=:id"),
        {"id": approval_id}
    ).fetchone()
    assert_owned_by_tenant(row, user, resource_name="Approval")

    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d


@router.post("/jobs/{job_id}/approval/request", summary="Request approval for a job")
def request_approval(
    job_id: str,
    body:   ApprovalRequestBody,
    user:   dict = Depends(require_permission(
        "create:job", "start:job", "manage:tenant_settings"
    )),
    db:     Session = Depends(get_db),
):
    job_row = db.execute(
        text("SELECT id, tenant_id FROM migration_jobs WHERE id=:id"),
        {"id": job_id}
    ).fetchone()
    assert_owned_by_tenant(job_row, user, resource_name="Job")

    existing = db.execute(
        text("""
            SELECT id FROM migration_approvals
            WHERE job_id=:jid AND status='pending'
        """),
        {"jid": job_id}
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="An approval request is already pending for this job.")

    approval_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            INSERT INTO migration_approvals
                (id, job_id, tenant_id, requested_by, reason, status, requested_at)
            VALUES (:id, :jid, :tid, :by, :reason, 'pending', :now)
        """),
        {
            "id": approval_id, "jid": job_id, "tid": user["tenant_id"],
            "by": user["id"], "reason": body.reason, "now": now,
        }
    )
    db.execute(
        text("UPDATE migration_jobs SET status='awaiting_approval', updated_at=:now WHERE id=:id"),
        {"now": now, "id": job_id}
    )
    db.commit()

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.requested", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"reason": body.reason, "requested_by": user["email"]},
            db=db,
        )
    except Exception:
        pass

    logger.info("Approval requested", job_id=job_id, requested_by=user["id"])
    return {"approval_id": approval_id, "job_id": job_id, "status": "pending"}


@router.post("/jobs/{job_id}/approval/approve", summary="Approve a pending migration (admin only)")
def approve_job(
    job_id: str,
    body:   ApprovalReviewBody,
    user:   dict = Depends(require_permission("manage:tenant_settings")),
    db:     Session = Depends(get_db),
):
    approval = db.execute(
        text("""
            SELECT id, tenant_id FROM migration_approvals
            WHERE job_id=:jid AND status='pending'
            ORDER BY requested_at DESC LIMIT 1
        """),
        {"jid": job_id}
    ).fetchone()
    assert_owned_by_tenant(approval, user, resource_name="Approval")

    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            UPDATE migration_approvals SET
                status='approved', approver_id=:approver, comments=:comments, reviewed_at=:now
            WHERE id=:id
        """),
        {"approver": user["id"], "comments": body.comments, "now": now, "id": approval.id}
    )
    db.execute(
        text("UPDATE migration_jobs SET status='planning', updated_at=:now WHERE id=:id"),
        {"now": now, "id": job_id}
    )
    db.commit()

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.approved", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"approved_by": user["email"], "comments": body.comments},
            db=db,
        )
    except Exception:
        pass

    logger.info("Job approved", job_id=job_id, approved_by=user["id"])
    return {"job_id": job_id, "status": "approved", "approved_by": user["email"]}


@router.post("/jobs/{job_id}/approval/reject", summary="Reject a pending migration (admin only)")
def reject_job(
    job_id: str,
    body:   ApprovalReviewBody,
    user:   dict = Depends(require_permission("manage:tenant_settings")),
    db:     Session = Depends(get_db),
):
    approval = db.execute(
        text("""
            SELECT id, tenant_id FROM migration_approvals
            WHERE job_id=:jid AND status='pending'
            ORDER BY requested_at DESC LIMIT 1
        """),
        {"jid": job_id}
    ).fetchone()
    assert_owned_by_tenant(approval, user, resource_name="Approval")

    if not body.comments:
        raise HTTPException(status_code=400, detail="A reason is required when rejecting a migration.")

    now = datetime.datetime.utcnow()
    db.execute(
        text("""
            UPDATE migration_approvals SET
                status='rejected', approver_id=:approver, comments=:comments, reviewed_at=:now
            WHERE id=:id
        """),
        {"approver": user["id"], "comments": body.comments, "now": now, "id": approval.id}
    )
    db.execute(
        text("UPDATE migration_jobs SET status='cancelled', error_message=:msg, updated_at=:now WHERE id=:id"),
        {"msg": f"Approval rejected: {body.comments}", "now": now, "id": job_id}
    )
    db.commit()

    try:
        from backend.kernel.event_bus.event_bus import EventBus
        EventBus.publish(
            event_type="approval.rejected", source_service="enterprise_security",
            resource_type="job", resource_id=job_id,
            payload={"rejected_by": user["email"], "comments": body.comments},
            db=db,
        )
    except Exception:
        pass

    logger.info("Job rejected", job_id=job_id, rejected_by=user["id"])
    return {"job_id": job_id, "status": "rejected", "rejected_by": user["email"]}
