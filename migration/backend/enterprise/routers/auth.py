"""
Auth Router
File: migration/backend/enterprise/routers/auth.py

Endpoints:
    POST /auth/register          → create tenant + admin user
    POST /auth/login             → get JWT token
    POST /auth/logout            → revoke session
    POST /auth/change-password   → change own password
    POST /auth/forgot-password   → request a password reset email
    POST /auth/reset-password    → complete a password reset with a token
    GET  /auth/me                → current user info
    POST /auth/api-keys          → create API key
    GET  /auth/api-keys          → list API keys
    DELETE /auth/api-keys/{id}   → revoke API key

CHANGES IN THIS VERSION:
  - REMOVED the invitation-based user flow (POST /auth/invite,
    POST /auth/invite/accept, GET /auth/invitations). Per product
    decision, admins now create user accounts directly with
    POST /tenants/{id}/users (see enterprise/routers/tenants.py) instead
    of sending an invite the person has to accept. invitation_service.py
    is left in the codebase but is no longer imported/used anywhere - kept
    only in case this decision is reversed later.
  - ADDED POST /auth/forgot-password and POST /auth/reset-password. These
    were referenced by shared/auth/auth_email.py's integration notes and
    by the frontend's ForgotPassword.tsx page, but never actually existed
    on the backend - that request was 404ing. Uses the existing
    password_reset_tokens table (was already in the schema, unused).
  - Added POST /auth/change-password in a previous pass, which didn't
    exist at all despite the frontend Settings page calling it.
"""

import uuid
import secrets
import hashlib
import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr
from typing import Optional

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import (
    get_current_user, require_permission, CurrentUser,
    verify_password, create_token, hash_password
)
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.enterprise.saas.tenants.tenant_service import TenantService
from backend.shared.auth.auth_email import send_password_reset_email, send_password_changed_notice

router      = APIRouter(prefix="/auth", tags=["Authentication"])
tenant_svc  = TenantService()

# Password reset tokens are valid for 1 hour. Matches the copy in
# send_password_reset_email()'s email body - keep both in sync if changed.
RESET_TOKEN_TTL_MINUTES = 60


class RegisterRequest(BaseModel):
    tenant_name:   str
    tenant_slug:   str
    full_name:     str
    email:         str
    password:      str
    plan_name:     str = "free"


class LoginRequest(BaseModel):
    email:    str
    password: str


class CreateApiKeyRequest(BaseModel):
    name:       str
    role:       str = "api_client"
    expires_in_days: Optional[int] = 365


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordConfirmRequest(BaseModel):
    token:        str
    new_password: str


@router.post("/register", summary="Create tenant and admin user")
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """
    Creates a new tenant workspace and the first admin user.
    Returns a JWT token — the admin is immediately logged in.
    """
    # Check slug uniqueness
    existing = tenant_svc.get_tenant_by_slug(db, req.tenant_slug)
    if existing:
        raise HTTPException(status_code=400, detail=f"Slug '{req.tenant_slug}' is already taken")

    # Check email uniqueness
    email_exists = db.execute(
        text("SELECT id FROM users WHERE email=:email"),
        {"email": req.email}
    ).fetchone()
    if email_exists:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create tenant
    tenant = tenant_svc.create_tenant(
        db=db,
        name=req.tenant_name,
        slug=req.tenant_slug,
        plan_name=req.plan_name,
    )

    # Create admin user
    user = tenant_svc.create_user(
        db=db,
        tenant_id=tenant["id"],
        email=req.email,
        password=req.password,
        full_name=req.full_name,
        role="tenant_admin",
    )

    token = create_token(
        user_id=user["id"],
        tenant_id=tenant["id"],
        role="tenant_admin",
        email=user["email"],
    )

    AuditTrail.log(
        db=db, action="tenant.register",
        tenant_id=tenant["id"], user_id=user["id"],
        resource_type="tenant", resource_id=tenant["id"],
        new_value={"name": tenant["name"], "slug": tenant["slug"]},
        request=request,
    )

    return {
        "token":     token,
        "user":      {k: v for k, v in user.items() if k != "password_hash"},
        "tenant":    tenant,
        "message":   f"Welcome to {tenant['name']}! Your admin account is ready.",
    }


@router.post("/login", summary="Login and get JWT token")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticate with email + password. Returns a JWT Bearer token."""
    user_row = tenant_svc.get_user_by_email(db, req.email)

    if not user_row or not verify_password(req.password, user_row.get("password_hash", "")):
        AuditTrail.log(
            db=db, action="auth.login.failed",
            new_value={"email": req.email},
            status="failed", error_msg="Invalid credentials",
            request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    tenant_svc.record_login(db, user_row["id"])

    token = create_token(
        user_id=user_row["id"],
        tenant_id=str(user_row["tenant_id"]),
        role=user_row["role"],
        email=user_row["email"],
    )

    AuditTrail.log(
        db=db, action="auth.login",
        tenant_id=str(user_row["tenant_id"]),
        user_id=user_row["id"],
        request=request,
    )

    return {
        "token":      token,
        "token_type": "bearer",
        "expires_in": 86400,
        "user": {
            "id":        user_row["id"],
            "email":     user_row["email"],
            "full_name": user_row.get("full_name"),
            "role":      user_row["role"],
            "tenant_id": str(user_row["tenant_id"]),
        },
    }


@router.post("/logout", summary="Revoke current session")
def logout(
    request: Request,
    user:    CurrentUser = Depends(get_current_user),
    db:      Session     = Depends(get_db)
):
    AuditTrail.log(
        db=db, action="auth.logout",
        tenant_id=user.tenant_id, user_id=user.user_id,
        request=request,
    )
    return {"message": "Logged out successfully"}


@router.get("/me", summary="Get current user info")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns the authenticated user's profile and permissions."""
    user_row = tenant_svc.get_user(db, user.user_id)
    return {
        "user_id":     user.user_id,
        "email":       user.email,
        "role":        user.role,
        "tenant_id":   user.tenant_id,
        "permissions": user.permissions,
        "full_name":   user_row.get("full_name") if user_row else None,
    }


@router.post("/change-password", summary="Change your own password")
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db:   Session      = Depends(get_db),
):
    """Change the authenticated user's own password. Requires the current password."""
    user_row = tenant_svc.get_user(db, user.user_id)
    if not user_row or not verify_password(req.current_password, user_row.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    db.execute(
        text("UPDATE users SET password_hash=:ph WHERE id=:id"),
        {"ph": hash_password(req.new_password), "id": user.user_id}
    )
    db.commit()

    AuditTrail.log(
        db=db, action="auth.password_changed",
        tenant_id=user.tenant_id, user_id=user.user_id,
        request=request,
    )
    return {"message": "Password changed successfully"}


@router.post("/forgot-password", summary="Request a password reset email")
def forgot_password(req: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """
    Always returns a generic success message whether or not the email
    exists, so this endpoint can't be used to enumerate registered emails.
    If the email exists, a reset token is generated, hashed, stored in
    password_reset_tokens, and emailed to the user (or logged to console
    if SMTP isn't configured - see .env.example).
    """
    generic_response = {
        "message": "If an account exists for that email, a password reset link has been sent."
    }

    user_row = tenant_svc.get_user_by_email(db, req.email)
    if not user_row:
        # Deliberately identical response + no error - don't leak whether
        # the email is registered.
        return generic_response

    raw_token  = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)

    db.execute(
        text("""
            INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at)
            VALUES (:id, :uid, :hash, :exp, :now)
        """),
        {
            "id": str(uuid.uuid4()), "uid": user_row["id"], "hash": token_hash,
            "exp": expires_at, "now": datetime.datetime.utcnow(),
        }
    )
    db.commit()

    try:
        send_password_reset_email(db, to_email=user_row["email"], reset_token=raw_token)
    except Exception as e:
        # Token is already stored - log but still return success so we
        # don't leak email-delivery failures to an unauthenticated caller.
        from backend.shared.config.logging import logger
        logger.warning("Password reset email failed to send", email=req.email, error=str(e))

    AuditTrail.log(
        db=db, action="auth.password_reset_requested",
        tenant_id=str(user_row["tenant_id"]), user_id=user_row["id"],
        request=request,
    )
    return generic_response


@router.post("/reset-password", summary="Complete a password reset using a token")
def reset_password_confirm(req: ResetPasswordConfirmRequest, request: Request, db: Session = Depends(get_db)):
    """Consumes a token from POST /auth/forgot-password and sets a new password."""
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    row = db.execute(
        text("""
            SELECT id, user_id FROM password_reset_tokens
            WHERE token_hash=:hash AND used_at IS NULL AND expires_at > :now
        """),
        {"hash": token_hash, "now": datetime.datetime.utcnow()}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")

    user_row = tenant_svc.get_user(db, str(row.user_id))
    if not user_row:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")

    db.execute(
        text("UPDATE users SET password_hash=:ph, force_password_change=FALSE, updated_at=:now WHERE id=:id"),
        {"ph": hash_password(req.new_password), "now": datetime.datetime.utcnow(), "id": row.user_id}
    )
    db.execute(
        text("UPDATE password_reset_tokens SET used_at=:now WHERE id=:id"),
        {"now": datetime.datetime.utcnow(), "id": row.id}
    )
    # Revoke existing sessions - anyone with an old session token gets
    # signed out, matching what should happen on any password change.
    db.execute(
        text("UPDATE user_sessions SET is_revoked=TRUE WHERE user_id=:id"),
        {"id": row.user_id}
    )
    db.commit()

    try:
        send_password_changed_notice(db, to_email=user_row["email"], name=user_row.get("full_name"))
    except Exception as e:
        from backend.shared.config.logging import logger
        logger.warning("Password-changed notice failed to send", email=user_row["email"], error=str(e))

    AuditTrail.log(
        db=db, action="auth.password_reset_completed",
        tenant_id=str(user_row["tenant_id"]), user_id=str(row.user_id),
        request=request,
    )
    return {"message": "Password reset successfully. You can now sign in with your new password."}


@router.post("/api-keys", summary="Create an API key")
def create_api_key(
    req:     CreateApiKeyRequest,
    request: Request,
    user:    CurrentUser = Depends(require_permission("settings:manage")),
    db:      Session     = Depends(get_db),
):
    """
    Create an API key for machine-to-machine access.
    The full key is returned ONCE — it is not stored in plaintext.
    Store it securely in your application's secrets manager.
    """
    raw_key     = "mk_" + secrets.token_urlsafe(40)
    key_hash    = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix  = raw_key[:12]
    expires_at  = (
        datetime.datetime.utcnow() + datetime.timedelta(days=req.expires_in_days)
        if req.expires_in_days else None
    )
    kid = str(uuid.uuid4())
    now = datetime.datetime.utcnow()

    db.execute(
        text("""
            INSERT INTO api_keys
                (id, tenant_id, user_id, name, key_prefix, key_hash,
                 role, expires_at, is_active, created_at)
            VALUES
                (:id, :tid, :uid, :name, :prefix, :hash,
                 :role, :exp, TRUE, :now)
        """),
        {
            "id": kid, "tid": user.tenant_id, "uid": user.user_id,
            "name": req.name, "prefix": key_prefix, "hash": key_hash,
            "role": req.role, "exp": expires_at, "now": now,
        }
    )
    db.commit()

    AuditTrail.log(
        db=db, action="api_key.create",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="api_key", resource_id=kid,
        new_value={"name": req.name, "role": req.role},
        request=request,
    )

    return {
        "id":         kid,
        "name":       req.name,
        "key":        raw_key,   # Only returned once
        "key_prefix": key_prefix,
        "role":       req.role,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "warning":    "Store this key securely — it will NOT be shown again.",
    }


@router.get("/api-keys", summary="List API keys (prefixes only)")
def list_api_keys(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    db:   Session     = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT id, name, key_prefix, role, last_used_at, expires_at, is_active, created_at
            FROM api_keys WHERE tenant_id=:tid
            ORDER BY created_at DESC
        """),
        {"tid": user.tenant_id}
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return result


@router.delete("/api-keys/{key_id}", summary="Revoke an API key")
def revoke_api_key(
    key_id:  str,
    request: Request,
    user:    CurrentUser = Depends(require_permission("settings:manage")),
    db:      Session     = Depends(get_db),
):
    db.execute(
        text("UPDATE api_keys SET is_active=FALSE WHERE id=:id AND tenant_id=:tid"),
        {"id": key_id, "tid": user.tenant_id}
    )
    db.commit()
    AuditTrail.log(
        db=db, action="api_key.revoke",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="api_key", resource_id=key_id,
        request=request,
    )
    return {"revoked": key_id}
