"""
Approval Workflow Router
File: migration/backend/enterprise/routers/approvals.py

Endpoints:
    POST /jobs/{id}/approval/request   → request approval for a job
    POST /jobs/{id}/approval/approve   → approve (Migration Admin / Tenant Admin only)
    POST /jobs/{id}/approval/reject    → reject with reason
    GET  /jobs/{id}/approval           → get approval status
    GET  /approvals/pending            → list all pending approvals for tenant
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.enterprise.saas.approval.approval_service import ApprovalService

router       = APIRouter(tags=["Approval Workflow"])
approval_svc = ApprovalService()


class RequestApprovalBody(BaseModel):
    auto_approve_rules: Optional[Dict] = None


class ReviewBody(BaseModel):
    notes: Optional[str] = None


class RejectBody(BaseModel):
    notes: str    # Required — must give reason for rejection


@router.post("/jobs/{job_id}/approval/request",
             summary="Request approval before executing a migration")
def request_approval(
    job_id:  str,
    req:     RequestApprovalBody,
    request: Request,
    user:    CurrentUser = Depends(require_permission("jobs:create")),
    db:      Session     = Depends(get_db),
):
    """
    Submit a migration job for approval before execution.

    After calling this, the job status changes to 'pending_approval'.
    A Migration Admin or Tenant Admin must review and approve/reject it.

    Auto-approval rules (optional):
      Pass auto_approve_rules to enable automatic approval for simple jobs:
      {
        "require_approval_for_small_jobs": false   → small jobs auto-approve
      }

    If auto-approved: job moves directly to 'queued' status.
    If pending: approval must come from a reviewer before job can run.
    """
    try:
        result = approval_svc.request_approval(
            db=db,
            job_id=job_id,
            tenant_id=user.tenant_id,
            requested_by_id=user.user_id,
            auto_approve_if=req.auto_approve_rules or {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="job.approval.request",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"auto_approved": result.get("auto_approved")},
        request=request,
    )
    return result


@router.post("/jobs/{job_id}/approval/approve",
             summary="Approve a migration job for execution")
def approve_job(
    job_id:  str,
    req:     ReviewBody,
    request: Request,
    user:    CurrentUser = Depends(require_permission("jobs:approve")),
    db:      Session     = Depends(get_db),
):
    """
    Approve a pending migration. Requires migration_admin or tenant_admin role.

    After approval the job status changes to 'queued' and workers
    can begin processing its chunks.
    """
    try:
        result = approval_svc.approve(
            db=db,
            job_id=job_id,
            reviewed_by_id=user.user_id,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="job.approval.approved",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"notes": req.notes},
        request=request,
    )
    return result


@router.post("/jobs/{job_id}/approval/reject",
             summary="Reject a migration job")
def reject_job(
    job_id:  str,
    req:     RejectBody,
    request: Request,
    user:    CurrentUser = Depends(require_permission("jobs:approve")),
    db:      Session     = Depends(get_db),
):
    """
    Reject a pending migration. A reason (notes) is required.
    The job status returns to 'rejected' and the requester must
    revise and re-submit.
    """
    try:
        result = approval_svc.reject(
            db=db,
            job_id=job_id,
            reviewed_by_id=user.user_id,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="job.approval.rejected",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"reason": req.notes},
        request=request,
    )
    return result


@router.get("/jobs/{job_id}/approval", summary="Get approval status for a job")
def get_approval(
    job_id: str,
    user:   CurrentUser = Depends(require_permission("jobs:read")),
    db:     Session     = Depends(get_db),
):
    """Returns the latest approval record for a job."""
    result = approval_svc.get_approval_status(db, job_id)
    if not result:
        return {"job_id": job_id, "approval_status": "not_requested",
                "message": "No approval has been requested for this job"}
    return result


@router.get("/approvals/pending", summary="List all pending approvals for your tenant")
def list_pending(
    user: CurrentUser = Depends(require_permission("jobs:approve")),
    db:   Session     = Depends(get_db),
):
    """
    Returns all migration jobs waiting for approval in your tenant.
    Used by Migration Admins and Tenant Admins to review the queue.
    """
    return approval_svc.list_pending(db, user.tenant_id)
