"""
Secrets Router
File: migration/backend/enterprise/routers/secrets.py

Endpoints:
    POST   /secrets/{key}         → store or update a secret
    GET    /secrets               → list key names (never values)
    DELETE /secrets/{key}         → delete a secret
    POST   /secrets/{key}/verify  → verify value matches without revealing it
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.enterprise.security.secrets.secrets_manager import SecretsManager

router  = APIRouter(prefix="/secrets", tags=["Secrets Manager"])
sm      = SecretsManager()


class StoreSecretRequest(BaseModel):
    value:       str
    description: Optional[str] = None


class VerifySecretRequest(BaseModel):
    value: str


@router.post("/{key_name}", summary="Store or update a secret")
def store_secret(
    key_name: str,
    req:      StoreSecretRequest,
    request:  Request,
    user:     CurrentUser = Depends(require_permission("settings:manage")),
    db:       Session     = Depends(get_db),
):
    """
    Store a secret value encrypted with AES-256.
    The value is NEVER returned via API after storage.
    Use /verify to check if a value matches without revealing it.

    Common use cases:
      - SMTP_PASSWORD
      - SLACK_WEBHOOK_TOKEN
      - WEBHOOK_SIGNING_SECRET
      - CUSTOM_ENCRYPTION_KEY
    """
    result = sm.set_secret(
        db=db,
        tenant_id=user.tenant_id,
        key_name=key_name,
        value=req.value,
        description=req.description,
        created_by=user.user_id,
    )
    AuditTrail.log(
        db=db, action="secret.set",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="secret",
        new_value={"key_name": key_name},
        request=request,
    )
    return result


@router.get("", summary="List secret keys (no values)")
def list_secrets(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    db:   Session     = Depends(get_db),
):
    """Returns key names and descriptions only. Values are never returned."""
    return sm.list_keys(db, user.tenant_id)


@router.delete("/{key_name}", summary="Delete a secret")
def delete_secret(
    key_name: str,
    request:  Request,
    user:     CurrentUser = Depends(require_permission("settings:manage")),
    db:       Session     = Depends(get_db),
):
    result = sm.delete_secret(db, user.tenant_id, key_name)
    AuditTrail.log(
        db=db, action="secret.delete",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="secret",
        new_value={"key_name": key_name},
        request=request,
    )
    return result


@router.post("/{key_name}/verify", summary="Verify a value matches the stored secret")
def verify_secret(
    key_name: str,
    req:      VerifySecretRequest,
    user:     CurrentUser = Depends(require_permission("settings:manage")),
    db:       Session     = Depends(get_db),
):
    """
    Verify that a provided value matches the stored secret.
    Returns {matches: true/false} without revealing the stored value.
    """
    matches = sm.verify_secret(db, user.tenant_id, key_name, req.value)
    return {"key_name": key_name, "matches": matches}
