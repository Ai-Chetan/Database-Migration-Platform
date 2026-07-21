"""
Tenants & Users Router
File: migration/backend/enterprise/routers/tenants.py

CHANGES IN THIS VERSION:
  Added two endpoints that were missing, needed for the admin-driven
  user management flow (create a user directly rather than only via
  invitation-acceptance, and admin-initiated password reset):
    POST /tenants/{id}/users                      → create user directly
    POST /tenants/{id}/users/{uid}/reset-password  → admin resets password

Endpoints:
    GET  /tenants/{id}                    → get tenant detail + usage
    GET  /tenants/{id}/users              → list users in tenant
    POST /tenants/{id}/users              → create a user directly (NEW)
    POST /tenants/{id}/users/{uid}/reset-password → admin password reset (NEW)
    PUT  /tenants/{id}/users/{uid}/role   → change user role
    DELETE /tenants/{id}/users/{uid}      → deactivate user
    GET  /tenants/{id}/usage              → usage statistics
    GET  /tenants/{id}/limits             → check plan limits
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.enterprise.saas.tenants.tenant_service import TenantService

router     = APIRouter(prefix="/tenants", tags=["Tenants & Users"])
tenant_svc = TenantService()


class UpdateRoleRequest(BaseModel):
    role: str


class CreateUserRequest(BaseModel):
    email:     str
    password:  str
    full_name: str = None
    role:      str = "migration_operator"


class ResetPasswordRequest(BaseModel):
    new_password: str


@router.get("/{tenant_id}", summary="Get tenant detail")
def get_tenant(
    tenant_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    """Get tenant details. Users can only view their own tenant."""
    if user.tenant_id != tenant_id and not user.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")
    tenant = tenant_svc.get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/{tenant_id}/users", summary="List all users in tenant")
def list_users(
    tenant_id: str,
    user: CurrentUser = Depends(require_permission("users:read")),
    db:   Session     = Depends(get_db),
):
    if user.tenant_id != tenant_id and not user.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tenant_svc.list_users(db, tenant_id)


@router.post("/{tenant_id}/users", summary="Create a user directly (admin action)")
def create_user(
    tenant_id: str,
    req:       CreateUserRequest,
    request:   Request,
    current:   CurrentUser = Depends(require_permission("users:create")),
    db:        Session     = Depends(get_db),
):
    """
    Creates a user directly within the tenant without requiring an
    invitation-acceptance flow. Enforces the tenant's max_users plan limit.
    Password is provided directly by the admin (e.g. a generated temporary
    password shown once to the admin to relay to the new user).
    """
    if current.tenant_id != tenant_id and not current.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")

    limit = tenant_svc.check_limit(db, tenant_id, "users")
    if not limit["allowed"]:
        raise HTTPException(status_code=402, detail=limit["reason"])

    valid_roles = {"platform_admin", "tenant_admin", "migration_admin",
                   "migration_operator", "read_only", "auditor", "api_client"}
    if req.role not in valid_roles:
        raise HTTPException(status_code=400,
                            detail=f"Invalid role. Must be one of: {sorted(valid_roles)}")

    existing_users = tenant_svc.list_users(db, tenant_id)
    if any(u["email"].lower() == req.email.lower() for u in existing_users):
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    result = tenant_svc.create_user(
        db=db, tenant_id=tenant_id, email=req.email,
        password=req.password, full_name=req.full_name, role=req.role,
    )

    AuditTrail.log(
        db=db, action="user.create",
        tenant_id=tenant_id, user_id=current.user_id,
        resource_type="user", resource_id=result["id"],
        new_value={"email": req.email, "role": req.role}, request=request,
    )
    return result


@router.post("/{tenant_id}/users/{user_id}/reset-password", summary="Admin: reset a user's password")
def reset_password(
    tenant_id: str,
    user_id:   str,
    req:       ResetPasswordRequest,
    request:   Request,
    current:   CurrentUser = Depends(require_permission("users:update")),
    db:        Session     = Depends(get_db),
):
    """Admin-initiated password reset. Requires the new password directly."""
    if current.tenant_id != tenant_id and not current.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")

    from backend.enterprise.security.rbac.auth import hash_password
    from sqlalchemy import text as _text
    import datetime as _dt

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    new_hash = hash_password(req.new_password)
    db.execute(
        _text("UPDATE users SET password_hash=:h, updated_at=:now WHERE id=:id AND tenant_id=:tid"),
        {"h": new_hash, "now": _dt.datetime.utcnow(), "id": user_id, "tid": tenant_id}
    )
    db.commit()

    AuditTrail.log(
        db=db, action="user.password_reset",
        tenant_id=tenant_id, user_id=current.user_id,
        resource_type="user", resource_id=user_id, request=request,
    )
    return {"message": "Password reset successfully.", "user_id": user_id}


@router.put("/{tenant_id}/users/{user_id}/role", summary="Change user role")
def update_role(
    tenant_id: str,
    user_id:   str,
    req:       UpdateRoleRequest,
    request:   Request,
    current:   CurrentUser = Depends(require_permission("users:update")),
    db:        Session     = Depends(get_db),
):
    if current.tenant_id != tenant_id and not current.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Cannot demote yourself
    if user_id == current.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    try:
        result = tenant_svc.update_user_role(db, user_id, req.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="user.role.update",
        tenant_id=tenant_id, user_id=current.user_id,
        resource_type="user", resource_id=user_id,
        new_value={"role": req.role}, request=request,
    )
    return result


@router.delete("/{tenant_id}/users/{user_id}", summary="Deactivate user")
def deactivate_user(
    tenant_id: str,
    user_id:   str,
    request:   Request,
    current:   CurrentUser = Depends(require_permission("users:delete")),
    db:        Session     = Depends(get_db),
):
    if current.tenant_id != tenant_id and not current.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")
    if user_id == current.user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    result = tenant_svc.deactivate_user(db, user_id)
    AuditTrail.log(
        db=db, action="user.deactivate",
        tenant_id=tenant_id, user_id=current.user_id,
        resource_type="user", resource_id=user_id,
        request=request,
    )
    return result


@router.get("/{tenant_id}/usage", summary="Get tenant usage statistics")
def get_usage(
    tenant_id: str,
    months:    int = 3,
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    if user.tenant_id != tenant_id and not user.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tenant_svc.get_usage(db, tenant_id, months=months)


@router.get("/{tenant_id}/limits", summary="Check plan limits")
def check_limits(
    tenant_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    """Shows current usage vs plan limits for all resource types."""
    if user.tenant_id != tenant_id and not user.can("*"):
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "tenant_id": tenant_id,
        "jobs":        tenant_svc.check_limit(db, tenant_id, "jobs"),
        "connections": tenant_svc.check_limit(db, tenant_id, "connections"),
        "users":       tenant_svc.check_limit(db, tenant_id, "users"),
    }
