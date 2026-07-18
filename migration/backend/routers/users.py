"""
Users Router
File: migration/backend/routers/users.py

Endpoints:
    GET    /users                       -> list users (admin only)
    POST   /users                       -> create user (admin only)
    GET    /users/{id}                  -> get one user
    PUT    /users/{id}                  -> update user
    DELETE /users/{id}                  -> deactivate user (soft delete)
    POST   /users/{id}/reset-password   -> admin resets a user's password
    POST   /users/{id}/activate         -> reactivate a deactivated user
    GET    /roles                       -> list all roles with permissions
"""

import datetime
import secrets
import string
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, EmailStr
from typing import Optional, List

from backend.shared.config.database import get_db
from backend.shared.auth.auth_service import AuthService, AuthError
from backend.shared.middleware.jwt_middleware import get_current_user, require_permission
from backend.shared.config.logging import logger

router = APIRouter(tags=["User Management"])

_ROLE_RANK = {
    "platform_admin":     1,
    "tenant_admin":       2,
    "migration_admin":    3,
    "migration_operator": 4,
    "read_only":          5,
    "auditor":            6,
    "api_client":         7,
}


class CreateUserRequest(BaseModel):
    email:                 EmailStr
    name:                  str
    role:                  str
    password:              Optional[str] = None
    send_welcome_email:    bool = True


class UpdateUserRequest(BaseModel):
    name:      Optional[str] = None
    role:      Optional[str] = None
    is_active: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    new_password:  Optional[str] = None
    force_change:  bool = True


def _generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(14))
        if (any(c.isupper() for c in pwd) and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd


def _check_role_assignable(caller_role: str, target_role: str) -> None:
    caller_rank = _ROLE_RANK.get(caller_role, 999)
    target_rank = _ROLE_RANK.get(target_role, 999)
    if target_rank < caller_rank:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign the role '{target_role}' as it is more "
                   f"privileged than your own role."
        )


def _row_to_user(row) -> dict:
    d = dict(row._mapping)
    d["id"] = str(d["id"])
    if d.get("created_by"):
        d["created_by"] = str(d["created_by"])
    for k in ("last_login", "created_at", "updated_at", "locked_until"):
        if d.get(k):
            d[k] = d[k].isoformat()
    d.pop("password_hash", None)
    return d


router_get_users_summary = "List all users in the tenant"

@router.get("/users", summary="List all users in the tenant")
def list_users(
    role:      Optional[str] = None,
    is_active: Optional[bool] = None,
    search:    Optional[str] = None,
    user:      dict = Depends(require_permission("manage:users")),
    db:        Session = Depends(get_db),
):
    conditions = ["tenant_id = :tid"]
    params = {"tid": user["tenant_id"]}

    if role:
        conditions.append("role = :role")
        params["role"] = role
    if is_active is not None:
        conditions.append("is_active = :active")
        params["active"] = is_active
    if search:
        conditions.append("(LOWER(name) LIKE :search OR LOWER(email) LIKE :search)")
        params["search"] = f"%{search.lower()}%"

    rows = db.execute(
        text(f"""
            SELECT id, tenant_id, email, name, role, is_active, force_password_change,
                   last_login, failed_login_attempts, locked_until, created_at, updated_at
            FROM users
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
        """),
        params
    ).fetchall()

    users = [_row_to_user(r) for r in rows]
    return {"total": len(users), "users": users}


@router.post("/users", summary="Create a new user")
def create_user(
    req:  CreateUserRequest,
    user: dict = Depends(require_permission("manage:users")),
    db:   Session = Depends(get_db),
):
    valid_roles = list(_ROLE_RANK.keys())
    if req.role not in valid_roles:
        raise HTTPException(status_code=400,
                            detail=f"Invalid role. Must be one of: {valid_roles}")

    _check_role_assignable(user["role"], req.role)

    existing = db.execute(
        text("SELECT id FROM users WHERE LOWER(email)=:email AND tenant_id=:tid"),
        {"email": req.email.lower(), "tid": user["tenant_id"]}
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    generated_password = None
    if req.password:
        error = AuthService.validate_password_strength(req.password)
        if error:
            raise HTTPException(status_code=400, detail=error)
        password = req.password
    else:
        password = _generate_password()
        generated_password = password

    password_hash = AuthService.hash_password(password)

    row = db.execute(
        text("""
            INSERT INTO users
                (id, tenant_id, email, name, password_hash, role, is_active,
                 force_password_change, created_by, created_at, updated_at)
            VALUES
                (gen_random_uuid(), :tid, :email, :name, :hash, :role, TRUE,
                 TRUE, :created_by, :now, :now)
            RETURNING id, tenant_id, email, name, role, is_active,
                      force_password_change, created_at, updated_at
        """),
        {
            "tid": user["tenant_id"], "email": req.email.lower(), "name": req.name,
            "hash": password_hash, "role": req.role, "created_by": user["id"],
            "now": datetime.datetime.utcnow(),
        }
    ).fetchone()
    db.commit()

    result = _row_to_user(row)

    if req.send_welcome_email:
        logger.info("Welcome email would be sent here", email=req.email,
                    temp_password_shown=generated_password is not None)

    logger.info("User created", email=req.email, role=req.role, created_by=user["id"])

    return {
        **result,
        "generated_password": generated_password,
        "note": "Store this password securely, it will not be shown again." if generated_password else None,
    }


@router.get("/users/{user_id}", summary="Get one user")
def get_user(
    user_id: str,
    user:    dict = Depends(require_permission("manage:users")),
    db:      Session = Depends(get_db),
):
    row = db.execute(
        text("""
            SELECT id, tenant_id, email, name, role, is_active, force_password_change,
                   last_login, failed_login_attempts, locked_until, created_at, updated_at
            FROM users WHERE id=:id AND tenant_id=:tid
        """),
        {"id": user_id, "tid": user["tenant_id"]}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user(row)


@router.put("/users/{user_id}", summary="Update a user")
def update_user(
    user_id: str,
    req:     UpdateUserRequest,
    user:    dict = Depends(require_permission("manage:users")),
    db:      Session = Depends(get_db),
):
    target = db.execute(
        text("SELECT role FROM users WHERE id=:id AND tenant_id=:tid"),
        {"id": user_id, "tid": user["tenant_id"]}
    ).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    if req.role and req.role != target[0]:
        valid_roles = list(_ROLE_RANK.keys())
        if req.role not in valid_roles:
            raise HTTPException(status_code=400,
                                detail=f"Invalid role. Must be one of: {valid_roles}")
        _check_role_assignable(user["role"], req.role)

    set_parts = ["updated_at=:now"]
    params: dict = {"now": datetime.datetime.utcnow(), "id": user_id}

    if req.name is not None:
        set_parts.append("name=:name"); params["name"] = req.name
    if req.role is not None:
        set_parts.append("role=:role"); params["role"] = req.role
    if req.is_active is not None:
        set_parts.append("is_active=:active"); params["active"] = req.is_active

    db.execute(text(f"UPDATE users SET {', '.join(set_parts)} WHERE id=:id"), params)
    db.commit()

    if req.is_active is False:
        AuthService.revoke_all_sessions(db, user_id, reason="deactivated")

    row = db.execute(
        text("""
            SELECT id, tenant_id, email, name, role, is_active, force_password_change,
                   last_login, created_at, updated_at
            FROM users WHERE id=:id
        """),
        {"id": user_id}
    ).fetchone()

    logger.info("User updated", user_id=user_id, updated_by=user["id"])
    return _row_to_user(row)


@router.delete("/users/{user_id}", summary="Deactivate a user (soft delete)")
def deactivate_user(
    user_id: str,
    user:    dict = Depends(require_permission("manage:users")),
    db:      Session = Depends(get_db),
):
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")

    row = db.execute(
        text("SELECT id FROM users WHERE id=:id AND tenant_id=:tid"),
        {"id": user_id, "tid": user["tenant_id"]}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")

    db.execute(
        text("UPDATE users SET is_active=FALSE, updated_at=:now WHERE id=:id"),
        {"now": datetime.datetime.utcnow(), "id": user_id}
    )
    db.commit()
    AuthService.revoke_all_sessions(db, user_id, reason="deactivated")

    logger.info("User deactivated", user_id=user_id, deactivated_by=user["id"])
    return {"message": "User deactivated successfully.", "user_id": user_id}


@router.post("/users/{user_id}/activate", summary="Reactivate a deactivated user")
def activate_user(
    user_id: str,
    user:    dict = Depends(require_permission("manage:users")),
    db:      Session = Depends(get_db),
):
    row = db.execute(
        text("SELECT id FROM users WHERE id=:id AND tenant_id=:tid"),
        {"id": user_id, "tid": user["tenant_id"]}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")

    db.execute(
        text("UPDATE users SET is_active=TRUE, failed_login_attempts=0, locked_until=NULL, updated_at=:now WHERE id=:id"),
        {"now": datetime.datetime.utcnow(), "id": user_id}
    )
    db.commit()
    return {"message": "User reactivated successfully.", "user_id": user_id}


@router.post("/users/{user_id}/reset-password", summary="Admin: reset a user's password")
def reset_user_password(
    user_id: str,
    req:     ResetPasswordRequest,
    user:    dict = Depends(require_permission("manage:users")),
    db:      Session = Depends(get_db),
):
    row = db.execute(
        text("SELECT id FROM users WHERE id=:id AND tenant_id=:tid"),
        {"id": user_id, "tid": user["tenant_id"]}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")

    generated_password = None
    if req.new_password:
        password = req.new_password
    else:
        password = _generate_password()
        generated_password = password

    try:
        AuthService.admin_reset_password(db, user_id, password, req.force_change)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    logger.info("Password reset by admin", user_id=user_id, reset_by=user["id"])

    return {
        "message": "Password reset successfully. All existing sessions have been revoked.",
        "generated_password": generated_password,
        "note": "Store this password securely, it will not be shown again." if generated_password else None,
    }


@router.get("/roles", summary="List all roles with their permissions")
def list_roles(db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT role_name, display_name, description, permissions, rank
            FROM role_definitions ORDER BY rank
        """)
    ).fetchall()

    roles = []
    for row in rows:
        d = dict(row._mapping)
        d["permissions"] = list(d["permissions"]) if d["permissions"] else []
        roles.append(d)

    return {"roles": roles}
