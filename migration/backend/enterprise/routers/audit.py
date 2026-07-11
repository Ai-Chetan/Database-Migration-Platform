"""
Audit Log Router
File: migration/backend/enterprise/routers/audit.py

Endpoints:
    GET /audit/logs              → query audit logs with filters
    GET /audit/logs/{resource}   → logs for a specific resource
    GET /audit/summary           → count of actions by type
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail

router = APIRouter(prefix="/audit", tags=["Audit Trail"])


@router.get("/logs", summary="Query audit logs")
def get_audit_logs(
    action:        Optional[str] = Query(None, description="Filter by action e.g. 'job.create'"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type e.g. 'job'"),
    resource_id:   Optional[str] = Query(None, description="Filter by specific resource UUID"),
    user_id:       Optional[str] = Query(None, description="Filter by user who performed action"),
    limit:         int           = Query(100, le=1000),
    offset:        int           = Query(0),
    user:          CurrentUser   = Depends(require_permission("audit:read")),
    db:            Session       = Depends(get_db),
):
    """
    Query the immutable audit log.

    Examples:
      GET /audit/logs?action=job.create           → all job creation events
      GET /audit/logs?resource_type=connection    → all connection events
      GET /audit/logs?resource_id=abc-123         → all events for a specific resource
      GET /audit/logs?user_id=xyz&action=job      → all job actions by a user

    Results are sorted newest first.
    Auditors and Tenant Admins can access this endpoint.
    """
    return AuditTrail.query(
        db=db,
        tenant_id=user.tenant_id if not user.can("*") else None,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/resource/{resource_type}/{resource_id}",
            summary="Get all audit events for a specific resource")
def get_resource_audit(
    resource_type: str,
    resource_id:   str,
    limit:         int         = Query(50, le=500),
    user:          CurrentUser = Depends(require_permission("audit:read")),
    db:            Session     = Depends(get_db),
):
    """
    Get complete audit history for a specific resource.

    Example: GET /audit/logs/resource/job/abc-123
    Shows everything that happened to job abc-123:
      - Who created it
      - Who requested approval
      - Who approved/rejected it
      - Who started/paused/cancelled it
      - Any mapping changes
    """
    return AuditTrail.query(
        db=db,
        tenant_id=user.tenant_id if not user.can("*") else None,
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
    )


@router.get("/summary", summary="Audit activity summary by action type")
def get_audit_summary(
    user: CurrentUser = Depends(require_permission("audit:read")),
    db:   Session     = Depends(get_db),
):
    """
    Returns counts of audit events grouped by action type.
    Useful for security dashboards and compliance reports.
    """
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT action, status, COUNT(*) as count,
                   MAX(created_at) as last_seen
            FROM audit_logs
            WHERE tenant_id = :tid
            GROUP BY action, status
            ORDER BY count DESC
            LIMIT 50
        """),
        {"tid": user.tenant_id}
    ).fetchall()

    summary = []
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("last_seen"), "isoformat"):
            d["last_seen"] = d["last_seen"].isoformat()
        summary.append(d)

    total_events = sum(r["count"] for r in summary)
    denied_events = sum(r["count"] for r in summary if r["status"] == "denied")
    failed_events = sum(r["count"] for r in summary if r["status"] == "failed")

    return {
        "tenant_id":     user.tenant_id,
        "total_events":  total_events,
        "denied_events": denied_events,
        "failed_events": failed_events,
        "by_action":     summary,
    }
