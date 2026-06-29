"""
Tenant & User Management
File: migration/backend/enterprise/saas/tenants/tenant_service.py

Handles:
  - Tenant registration and management
  - User creation, invitation, and management
  - Usage tracking (jobs, rows migrated, API calls)
  - Plan limit enforcement
"""

import uuid
import datetime
import secrets
import hashlib
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.enterprise.security.rbac.auth import hash_password
from backend.shared.config.logging import logger


class TenantService:

    # ── Tenants ───────────────────────────────────────────────────────────────

    def create_tenant(
        self,
        db:            Session,
        name:          str,
        slug:          str,
        plan_name:     str = "free",
        billing_email: str = None,
    ) -> dict:
        tid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()

        # Plan limits
        plan_limits = {
            "free":       {"max_users": 3,   "max_jobs": 10,  "max_connections": 5,  "max_workers": 2},
            "starter":    {"max_users": 10,  "max_jobs": 50,  "max_connections": 20, "max_workers": 4},
            "pro":        {"max_users": 50,  "max_jobs": 500, "max_connections": 100,"max_workers": 16},
            "enterprise": {"max_users": 999, "max_jobs": 999, "max_connections": 999,"max_workers": 999},
        }
        limits = plan_limits.get(plan_name, plan_limits["free"])

        db.execute(
            text("""
                INSERT INTO tenants
                    (id, name, slug, status, plan_name,
                     max_users, max_jobs, max_connections, max_workers,
                     billing_email, created_at, updated_at)
                VALUES
                    (:id, :name, :slug, 'active', :plan,
                     :mu, :mj, :mc, :mw,
                     :email, :now, :now)
            """),
            {
                "id": tid, "name": name, "slug": slug, "plan": plan_name,
                "mu": limits["max_users"], "mj": limits["max_jobs"],
                "mc": limits["max_connections"], "mw": limits["max_workers"],
                "email": billing_email, "now": now,
            }
        )
        db.commit()
        logger.info("Tenant created", tenant_id=tid, name=name, plan=plan_name)
        return self.get_tenant(db, tid)

    def get_tenant(self, db: Session, tenant_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM tenants WHERE id = :id"),
            {"id": tenant_id}
        ).fetchone()
        return self._row(row) if row else None

    def get_tenant_by_slug(self, db: Session, slug: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM tenants WHERE slug = :slug"),
            {"slug": slug}
        ).fetchone()
        return self._row(row) if row else None

    def list_tenants(self, db: Session) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM tenants ORDER BY created_at DESC")
        ).fetchall()
        return [self._row(r) for r in rows]

    def check_limit(self, db: Session, tenant_id: str, resource: str) -> Dict:
        """
        Check if tenant has reached a plan limit.
        resource: "jobs" | "connections" | "users"
        Returns {"allowed": True/False, "current": N, "limit": N}
        """
        tenant = self.get_tenant(db, tenant_id)
        if not tenant:
            return {"allowed": False, "current": 0, "limit": 0, "reason": "Tenant not found"}

        limit_map = {
            "jobs":        ("max_jobs",        "migration_jobs",    "tenant_id"),
            "connections": ("max_connections",  "connection_registry","tenant_id"),
            "users":       ("max_users",        "users",             "tenant_id"),
        }

        if resource not in limit_map:
            return {"allowed": True, "current": 0, "limit": 0}

        limit_col, table, tid_col = limit_map[resource]
        max_allowed = tenant.get(limit_col, 999)

        result = db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {tid_col} = :tid"),
            {"tid": tenant_id}
        ).fetchone()
        current = result[0] if result else 0

        return {
            "allowed": current < max_allowed,
            "current": current,
            "limit":   max_allowed,
            "reason":  None if current < max_allowed else
                       f"Plan limit reached: {current}/{max_allowed} {resource}. Upgrade your plan.",
        }

    # ── Users ─────────────────────────────────────────────────────────────────

    def create_user(
        self,
        db:        Session,
        tenant_id: str,
        email:     str,
        password:  str,
        full_name: str = None,
        role:      str = "migration_operator",
    ) -> dict:
        uid  = str(uuid.uuid4())
        now  = datetime.datetime.utcnow()
        hpwd = hash_password(password)

        db.execute(
            text("""
                INSERT INTO users
                    (id, tenant_id, email, password_hash, full_name, role, status, created_at, updated_at)
                VALUES
                    (:id, :tid, :email, :pwd, :name, :role, 'active', :now, :now)
            """),
            {
                "id": uid, "tid": tenant_id, "email": email,
                "pwd": hpwd, "name": full_name, "role": role, "now": now,
            }
        )
        db.commit()
        logger.info("User created", user_id=uid, email=email, role=role)
        return self.get_user(db, uid)

    def get_user(self, db: Session, user_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT id, tenant_id, email, full_name, role, status, last_login_at, created_at FROM users WHERE id = :id"),
            {"id": user_id}
        ).fetchone()
        return self._row(row) if row else None

    def get_user_by_email(self, db: Session, email: str) -> Optional[dict]:
        """Returns user WITH password_hash (for login verification only)."""
        row = db.execute(
            text("SELECT * FROM users WHERE email = :email AND status = 'active'"),
            {"email": email}
        ).fetchone()
        return self._row(row) if row else None

    def list_users(self, db: Session, tenant_id: str) -> List[dict]:
        rows = db.execute(
            text("""
                SELECT id, tenant_id, email, full_name, role, status, last_login_at, created_at
                FROM users WHERE tenant_id = :tid ORDER BY created_at DESC
            """),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_user_role(self, db: Session, user_id: str, new_role: str) -> dict:
        valid_roles = {"platform_admin","tenant_admin","migration_admin",
                       "migration_operator","read_only","auditor","api_client"}
        if new_role not in valid_roles:
            raise ValueError(f"Invalid role: {new_role}")
        db.execute(
            text("UPDATE users SET role=:role, updated_at=:now WHERE id=:id"),
            {"role": new_role, "now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()
        return self.get_user(db, user_id)

    def record_login(self, db: Session, user_id: str):
        db.execute(
            text("UPDATE users SET last_login_at=:now WHERE id=:id"),
            {"now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()

    def deactivate_user(self, db: Session, user_id: str) -> dict:
        db.execute(
            text("UPDATE users SET status='inactive', updated_at=:now WHERE id=:id"),
            {"now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()
        return {"user_id": user_id, "status": "inactive"}

    # ── Usage tracking ────────────────────────────────────────────────────────

    def increment_usage(
        self,
        db:        Session,
        tenant_id: str,
        metric:    str,
        amount:    int = 1,
    ):
        """
        Increment a usage metric for the current month.
        metric: jobs_created | jobs_completed | rows_migrated | api_calls
        """
        period = datetime.datetime.utcnow().strftime("%Y-%m")
        db.execute(
            text(f"""
                INSERT INTO tenant_usage (id, tenant_id, period_month, {metric}, created_at, updated_at)
                VALUES (:id, :tid, :period, :amount, :now, :now)
                ON CONFLICT (tenant_id, period_month)
                DO UPDATE SET {metric} = tenant_usage.{metric} + :amount, updated_at = :now
            """),
            {
                "id":     str(uuid.uuid4()),
                "tid":    tenant_id,
                "period": period,
                "amount": amount,
                "now":    datetime.datetime.utcnow(),
            }
        )
        try:
            db.commit()
        except Exception:
            db.rollback()

    def get_usage(self, db: Session, tenant_id: str, months: int = 3) -> List[dict]:
        rows = db.execute(
            text("""
                SELECT period_month, jobs_created, jobs_completed,
                       rows_migrated, gb_transferred, worker_hours, api_calls
                FROM tenant_usage
                WHERE tenant_id = :tid
                ORDER BY period_month DESC
                LIMIT :months
            """),
            {"tid": tenant_id, "months": months}
        ).fetchall()
        return [self._row(r) for r in rows]

    def _row(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d
