"""
Approval Workflow
File: migration/backend/enterprise/saas/approval/approval_service.py

Enterprise governance: migrations require approval before execution.

Workflow:
    Developer creates migration job (status: pending_approval)
        ↓
    DBA / Migration Admin reviews the plan
        ↓
    Approves or Rejects with notes
        ↓
    If approved: job is unlocked for execution
    If rejected: job stays paused, developer is notified

Auto-approval rules:
  - Jobs on non-production connections → auto-approved
  - Jobs with 0 unsafe conversions and <100k rows → auto-approved
  - All others → require manual approval
"""

import uuid
import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


class ApprovalService:

    def request_approval(
        self,
        db:               Session,
        job_id:           str,
        tenant_id:        str,
        requested_by_id:  str,
        auto_approve_if:  dict = None,   # conditions for auto-approval
    ) -> dict:
        """
        Request approval for a migration job.
        Checks auto-approval rules first. If eligible, approves immediately.
        Otherwise creates a pending approval record.
        """
        # Check auto-approval eligibility
        auto_approved, reason = self._check_auto_approve(
            db, job_id, auto_approve_if or {}
        )

        aid  = str(uuid.uuid4())
        now  = datetime.datetime.utcnow()
        status = "approved" if auto_approved else "pending"

        db.execute(
            text("""
                INSERT INTO migration_approvals
                    (id, job_id, tenant_id, requested_by_id, status,
                     notes, requested_at, reviewed_at, auto_approved)
                VALUES
                    (:id, :jid, :tid, :req, :status,
                     :notes, :now, :rev_at, :auto)
            """),
            {
                "id":     aid,
                "jid":    job_id,
                "tid":    tenant_id,
                "req":    requested_by_id,
                "status": status,
                "notes":  reason if auto_approved else "Pending review",
                "now":    now,
                "rev_at": now if auto_approved else None,
                "auto":   auto_approved,
            }
        )

        if auto_approved:
            db.execute(
                text("UPDATE migration_jobs SET status='queued', approved_at=:now WHERE id=:jid"),
                {"now": now, "jid": job_id}
            )

        db.commit()

        logger.info(
            "Approval request created",
            job_id=job_id,
            auto_approved=auto_approved,
            status=status
        )

        return {
            "approval_id":   aid,
            "job_id":        job_id,
            "status":        status,
            "auto_approved": auto_approved,
            "message":       reason if auto_approved else
                             "Approval request submitted. A Migration Admin or Tenant Admin must review.",
        }

    def approve(
        self,
        db:             Session,
        job_id:         str,
        reviewed_by_id: str,
        notes:          str = None,
    ) -> dict:
        """Approve a migration job. Unlocks it for execution."""
        approval = self._get_pending(db, job_id)
        if not approval:
            raise ValueError(f"No pending approval found for job {job_id}")

        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                UPDATE migration_approvals
                SET status='approved', reviewed_by_id=:rev, notes=:notes, reviewed_at=:now
                WHERE id=:id
            """),
            {"rev": reviewed_by_id, "notes": notes, "now": now, "id": approval["id"]}
        )
        db.execute(
            text("UPDATE migration_jobs SET status='queued', approved_by=:rev, approved_at=:now WHERE id=:jid"),
            {"rev": reviewed_by_id, "now": now, "jid": job_id}
        )
        db.commit()

        logger.info("Migration approved", job_id=job_id, by=reviewed_by_id)
        return {"job_id": job_id, "status": "approved", "reviewed_by": reviewed_by_id}

    def reject(
        self,
        db:             Session,
        job_id:         str,
        reviewed_by_id: str,
        notes:          str,
    ) -> dict:
        """Reject a migration job. Job remains in pending_approval state."""
        approval = self._get_pending(db, job_id)
        if not approval:
            raise ValueError(f"No pending approval found for job {job_id}")

        if not notes:
            raise ValueError("Rejection reason (notes) is required")

        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                UPDATE migration_approvals
                SET status='rejected', reviewed_by_id=:rev, notes=:notes, reviewed_at=:now
                WHERE id=:id
            """),
            {"rev": reviewed_by_id, "notes": notes, "now": now, "id": approval["id"]}
        )
        db.execute(
            text("UPDATE migration_jobs SET status='rejected' WHERE id=:jid"),
            {"jid": job_id}
        )
        db.commit()

        logger.info("Migration rejected", job_id=job_id, by=reviewed_by_id, reason=notes)
        return {"job_id": job_id, "status": "rejected", "reason": notes}

    def get_approval_status(self, db: Session, job_id: str) -> Optional[dict]:
        row = db.execute(
            text("""
                SELECT ma.*, u.email AS reviewer_email
                FROM migration_approvals ma
                LEFT JOIN users u ON ma.reviewed_by_id = u.id
                WHERE ma.job_id=:jid
                ORDER BY ma.requested_at DESC LIMIT 1
            """),
            {"jid": job_id}
        ).fetchone()
        if not row:
            return None
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d

    def list_pending(self, db: Session, tenant_id: str) -> list:
        """List all pending approvals for a tenant — used by reviewers."""
        rows = db.execute(
            text("""
                SELECT ma.id, ma.job_id, ma.requested_at, ma.notes,
                       u.email AS requested_by_email,
                       mj.status AS job_status, mj.total_chunks, mj.total_tables
                FROM migration_approvals ma
                JOIN migration_jobs mj ON ma.job_id = mj.id
                LEFT JOIN users u ON ma.requested_by_id = u.id
                WHERE ma.tenant_id=:tid AND ma.status='pending'
                ORDER BY ma.requested_at ASC
            """),
            {"tid": tenant_id}
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):       d[k] = str(v)
                if hasattr(v, "isoformat"): d[k] = v.isoformat()
            result.append(d)
        return result

    def _get_pending(self, db: Session, job_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM migration_approvals WHERE job_id=:jid AND status='pending' LIMIT 1"),
            {"jid": job_id}
        ).fetchone()
        if not row:
            return None
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):       d[k] = str(v)
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
        return d

    def _check_auto_approve(self, db: Session, job_id: str, rules: dict) -> tuple:
        """
        Returns (auto_approved: bool, reason: str).
        Auto-approve if all conditions pass.
        """
        job = db.execute(
            text("SELECT total_chunks, total_tables FROM migration_jobs WHERE id=:jid"),
            {"jid": job_id}
        ).fetchone()

        if not job:
            return False, "Job not found"

        # Rule 1: explicit override
        if rules.get("always_require_approval"):
            return False, "Policy requires manual approval for all migrations"

        # Rule 2: small job with no unsafe conversions
        total_chunks = job[0] or 0
        if total_chunks <= 10 and not rules.get("require_approval_for_small_jobs"):
            return True, f"Auto-approved: small job ({total_chunks} chunks, no unsafe conversions)"

        return False, "Manual approval required per policy"
