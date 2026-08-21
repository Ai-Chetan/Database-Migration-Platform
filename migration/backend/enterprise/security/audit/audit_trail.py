"""
Audit Trail
File: migration/backend/enterprise/security/audit/audit_trail.py

Immutable append-only audit log for every significant action.

Every action recorded:
  - Who did it (user_id, email)
  - What they did (action string like "job.create")
  - What resource (resource_type + resource_id)
  - Before/after state (old_value, new_value as JSONB)
  - When (created_at)
  - Where from (ip_address, user_agent)
  - Result (success | failed | denied)

IMPORTANT: Never UPDATE or DELETE rows from audit_logs.
The table is append-only. This is required for compliance.

Usage:
    from backend.enterprise.security.audit.audit_trail import AuditTrail

    # In a router:
    AuditTrail.log(
        db=db,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        action="job.create",
        resource_type="job",
        resource_id=str(job.id),
        new_value={"status": "pending", "name": job.name},
        request=request,
    )
"""

import uuid
import datetime
from typing import Optional, Any, Dict
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import Request
from backend.shared.config.logging import logger


class AuditTrail:

    @staticmethod
    def log(
        db:            Session,
        action:        str,
        tenant_id:     Optional[str] = None,
        user_id:       Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id:   Optional[str] = None,
        old_value:     Optional[Dict] = None,
        new_value:     Optional[Dict] = None,
        status:        str = "success",
        error_msg:     Optional[str] = None,
        request:       Optional[Request] = None,
        ip_address:    Optional[str] = None,
        user_agent:    Optional[str] = None,
    ):
        """
        Write one immutable audit log entry.
        Never raises — failures are logged to the app logger but don't crash callers.
        """
        # Extract request metadata if available
        if request and not ip_address:
            ip_address = AuditTrail._get_client_ip(request)
        if request and not user_agent:
            user_agent = request.headers.get("user-agent", "")

        import json
        try:
            details = None
            if old_value is not None or new_value is not None:
                details = json.dumps({"old_value": old_value, "new_value": new_value})

            db.execute(
                text("""
                    INSERT INTO audit_logs
                        (id, tenant_id, user_id, action,
                         resource_type, resource_id,
                         details,
                         ip_address, user_agent,
                         status, error_message, created_at)
                    VALUES
                        (:id, :tid, :uid, :action,
                         :rtype, CAST(:rid AS uuid),
                         CAST(:details AS jsonb),
                         :ip, :ua,
                         :status, :err, :now)
                """),
                {
                    "id":      str(uuid.uuid4()),
                    "tid":     tenant_id,
                    "uid":     user_id,
                    "action":  action,
                    "rtype":   resource_type,
                    "rid":     resource_id,
                    "details": details,
                    "ip":      ip_address,
                    "ua":      (user_agent or "")[:500],
                    "status":  status,
                    "err":     error_msg,
                    "now":     datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(
                "Audit log write failed (non-fatal)",
                action=action,
                error=str(e)
            )

    @staticmethod
    def log_denied(
        db: Session,
        user_id: str,
        tenant_id: str,
        action: str,
        resource_type: str = None,
        resource_id: str = None,
        request: Request = None,
    ):
        """Convenience method for logging denied permission attempts."""
        AuditTrail.log(
            db=db,
            action=action,
            tenant_id=tenant_id,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            status="denied",
            error_msg="Permission denied",
            request=request,
        )

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract real client IP, handling proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def query(
        db:            Session,
        tenant_id:     Optional[str] = None,
        user_id:       Optional[str] = None,
        action:        Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id:   Optional[str] = None,
        limit:         int = 100,
        offset:        int = 0,
    ) -> Dict:
        """Query audit logs with filters."""
        conditions = []
        params: Dict[str, Any] = {"lim": limit, "off": offset}

        if tenant_id:
            conditions.append("al.tenant_id = :tid")
            params["tid"] = tenant_id
        if user_id:
            conditions.append("al.user_id = :uid")
            params["uid"] = user_id
        if action:
            conditions.append("al.action ILIKE :action")
            params["action"] = f"%{action}%"
        if resource_type:
            conditions.append("al.resource_type = :rtype")
            params["rtype"] = resource_type
        if resource_id:
            conditions.append("al.resource_id = CAST(:rid AS uuid)")
            params["rid"] = resource_id

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = db.execute(
            text(f"""
                SELECT al.id, al.tenant_id, al.user_id, u.email AS user_email, al.action,
                       al.resource_type, al.resource_id,
                       al.details,
                       al.ip_address, al.status, al.error_message, al.created_at
                FROM audit_logs al
                LEFT JOIN users u ON u.id = al.user_id
                {where}
                ORDER BY al.created_at DESC
                LIMIT :lim OFFSET :off
            """),
            params
        ).fetchall()

        total_row = db.execute(
            text(f"SELECT COUNT(*) FROM audit_logs al {where}"),
            {k: v for k, v in params.items() if k not in ("lim", "off")}
        ).fetchone()

        entries = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):        d[k] = str(v)
                if hasattr(v, "isoformat"):  d[k] = v.isoformat()
            entries.append(d)

        return {
            "total":   total_row[0] if total_row else 0,
            "limit":   limit,
            "offset":  offset,
            "entries": entries,
        }
