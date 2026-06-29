"""
Auth Router
File: migration/backend/enterprise/routers/auth.py

Endpoints:
    POST /auth/register          → create tenant + admin user
    POST /auth/login             → get JWT token
    POST /auth/logout            → revoke session
    POST /auth/invite            → invite user to tenant
    POST /auth/invite/accept     → accept invitation, create account
    GET  /auth/me                → current user info
    POST /auth/api-keys          → create API key
    GET  /auth/api-keys          → list API keys
    DELETE /auth/api-keys/{id}   → revoke API key
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
from backend.enterprise.saas.invitations.invitation_service import InvitationService

router      = APIRouter(prefix="/auth", tags=["Authentication"])
tenant_svc  = TenantService()
invite_svc  = InvitationService()


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


class InviteRequest(BaseModel):
    email: str
    role:  str = "migration_operator"


class AcceptInviteRequest(BaseModel):
    token:     str
    full_name: str
    password:  str


class CreateApiKeyRequest(BaseModel):
    name:       str
    role:       str = "api_client"
    expires_in_days: Optional[int] = 365


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


@router.post("/invite", summary="Invite a user to your tenant")
def invite_user(
    req:     InviteRequest,
    request: Request,
    user:    CurrentUser = Depends(require_permission("users:create")),
    db:      Session     = Depends(get_db),
):
    """
    Invite someone to join your tenant workspace.
    Returns a token that the invitee uses to create their account.
    In production: email the invite_url to the user.
    """
    try:
        result = invite_svc.create_invitation(
            db=db,
            tenant_id=user.tenant_id,
            invited_by=user.user_id,
            email=req.email,
            role=req.role,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="user.invite",
        tenant_id=user.tenant_id, user_id=user.user_id,
        new_value={"email": req.email, "role": req.role},
        request=request,
    )
    return result


@router.post("/invite/accept", summary="Accept an invitation and create account")
def accept_invite(req: AcceptInviteRequest, request: Request, db: Session = Depends(get_db)):
    """Accept an invitation token and create your user account."""
    try:
        result = invite_svc.accept_invitation(
            db=db,
            raw_token=req.token,
            full_name=req.full_name,
            password=req.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = create_token(
        user_id=result["user_id"],
        tenant_id=result["tenant_id"],
        role=result["role"],
        email=result["email"],
    )

    AuditTrail.log(
        db=db, action="user.invite.accepted",
        tenant_id=result["tenant_id"], user_id=result["user_id"],
        request=request,
    )

    return {**result, "token": token, "token_type": "bearer"}


@router.get("/invitations", summary="List pending invitations")
def list_invitations(
    user: CurrentUser = Depends(require_permission("users:read")),
    db:   Session     = Depends(get_db),
):
    return invite_svc.list_invitations(db, user.tenant_id)


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
