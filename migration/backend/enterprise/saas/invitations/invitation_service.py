"""
Invitation Service
File: migration/backend/enterprise/saas/invitations/invitation_service.py

BUG FIX APPLIED (this version):
  accept_invitation()'s INSERT INTO users included a 'status' column
  with literal 'active' — the same mistake found and fixed in
  tenant_service.py. Live database inspection confirmed the real users
  table has no 'status' column, only 'is_active BOOLEAN'. Fixed the one
  occurrence to use is_active=TRUE instead. user_invitations.status (a
  different, real column on the user_invitations table) was left
  untouched — it does exist and is used correctly throughout this file.

Handles user invitations to a tenant workspace.
Flow:
  1. Admin calls POST /auth/invite with email + role
  2. Service generates a secure token, stores hash in user_invitations
  3. (In production) Email is sent with invite link containing token
  4. Invitee calls POST /auth/invite/accept with token + password
  5. User account is created, invitation marked accepted
"""

import uuid
import secrets
import hashlib
import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.enterprise.security.rbac.auth import hash_password
from backend.shared.config.logging import logger


INVITE_EXPIRE_HOURS = 72


class InvitationService:

    def create_invitation(
        self,
        db:           Session,
        tenant_id:    str,
        invited_by:   str,
        email:        str,
        role:         str = "migration_operator",
    ) -> dict:
        """
        Create an invitation. Returns the invitation record plus
        the raw token (only time it's available — not stored in DB).
        """
        # Check for existing pending invitation
        existing = db.execute(
            text("""
                SELECT id FROM user_invitations
                WHERE tenant_id=:tid AND email=:email AND status='pending'
                AND expires_at > :now
            """),
            {"tid": tenant_id, "email": email, "now": datetime.datetime.utcnow()}
        ).fetchone()
        if existing:
            raise ValueError(f"A pending invitation already exists for {email}")

        # Check if user already exists
        user_exists = db.execute(
            text("SELECT id FROM users WHERE email=:email"),
            {"email": email}
        ).fetchone()
        if user_exists:
            raise ValueError(f"A user with email {email} already exists")

        # Generate secure token
        raw_token  = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        iid        = str(uuid.uuid4())
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=INVITE_EXPIRE_HOURS)

        db.execute(
            text("""
                INSERT INTO user_invitations
                    (id, tenant_id, invited_by_id, email, role,
                     token_hash, expires_at, status, created_at)
                VALUES
                    (:id, :tid, :by, :email, :role,
                     :hash, :exp, 'pending', :now)
            """),
            {
                "id": iid, "tid": tenant_id, "by": invited_by,
                "email": email, "role": role,
                "hash": token_hash, "exp": expires_at,
                "now": datetime.datetime.utcnow(),
            }
        )
        db.commit()

        logger.info("Invitation created", email=email, tenant_id=tenant_id, role=role)

        return {
            "invitation_id": iid,
            "email":         email,
            "role":          role,
            "expires_at":    expires_at.isoformat(),
            "token":         raw_token,   # Return ONCE — caller sends this via email
            "invite_url":    f"/auth/invite/accept?token={raw_token}",
            "note":          "Send the invite_url to the user. The token is not stored in plaintext.",
        }

    def accept_invitation(
        self,
        db:        Session,
        raw_token: str,
        full_name: str,
        password:  str,
    ) -> dict:
        """
        Accept an invitation by providing the token, name, and new password.
        Creates the user account and marks the invitation accepted.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        invitation = db.execute(
            text("""
                SELECT * FROM user_invitations
                WHERE token_hash=:hash AND status='pending' AND expires_at > :now
            """),
            {"hash": token_hash, "now": datetime.datetime.utcnow()}
        ).fetchone()

        if not invitation:
            raise ValueError("Invalid or expired invitation token")

        inv = dict(invitation._mapping)

        # Create user
        uid  = str(uuid.uuid4())
        now  = datetime.datetime.utcnow()
        hpwd = hash_password(password)

        db.execute(
            text("""
                INSERT INTO users
                    (id, tenant_id, email, password_hash, full_name, role, is_active, created_at, updated_at)
                VALUES
                    (:id, :tid, :email, :pwd, :name, :role, TRUE, :now, :now)
            """),
            {
                "id": uid, "tid": inv["tenant_id"], "email": inv["email"],
                "pwd": hpwd, "name": full_name, "role": inv["role"], "now": now,
            }
        )

        # Mark invitation accepted
        db.execute(
            text("""
                UPDATE user_invitations
                SET status='accepted', accepted_at=:now
                WHERE id=:id
            """),
            {"now": now, "id": inv["id"]}
        )
        db.commit()

        logger.info("Invitation accepted", email=inv["email"], user_id=uid)

        return {
            "user_id":   uid,
            "email":     inv["email"],
            "role":      inv["role"],
            "tenant_id": str(inv["tenant_id"]),
            "status":    "active",
        }

    def list_invitations(self, db: Session, tenant_id: str) -> list:
        rows = db.execute(
            text("""
                SELECT id, email, role, status, expires_at, accepted_at, created_at
                FROM user_invitations
                WHERE tenant_id=:tid
                ORDER BY created_at DESC
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

    def cancel_invitation(self, db: Session, invitation_id: str, tenant_id: str) -> dict:
        db.execute(
            text("""
                UPDATE user_invitations SET status='cancelled'
                WHERE id=:id AND tenant_id=:tid AND status='pending'
            """),
            {"id": invitation_id, "tid": tenant_id}
        )
        db.commit()
        return {"invitation_id": invitation_id, "status": "cancelled"}
