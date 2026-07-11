"""
Tenants & Users Router
File: migration/backend/enterprise/routers/tenants.py

Endpoints:
    GET  /tenants/{id}           → get tenant detail + usage
    GET  /tenants/{id}/users     → list users in tenant
    PUT  /tenants/{id}/users/{uid}/role  → change user role
    DELETE /tenants/{id}/users/{uid}     → deactivate user
    GET  /tenants/{id}/usage     → usage statistics
    GET  /tenants/{id}/limits    → check plan limits
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
