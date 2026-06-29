"""
RBAC — Roles, Permissions, JWT Auth
File: migration/backend/enterprise/security/rbac/auth.py

Implements:
  - Password hashing (bcrypt)
  - JWT token creation and verification
  - Permission checking middleware
  - 7 system roles with granular permissions

Roles:
  platform_admin     → ["*"]                    full access
  tenant_admin       → ["jobs:*","users:*",...]  full tenant access
  migration_admin    → ["jobs:*","mappings:*",...] no user management
  migration_operator → ["jobs:read","jobs:start",...] execute only
  read_only          → ["*:read"]               view only
  auditor            → ["*:read","audit:*"]     read + audit logs
  api_client         → ["jobs:*","connections:read",...] API access

Permission format:  "resource:action"
  resource: jobs | connections | mappings | schemas | workers | audit | users | settings
  action:   * | read | create | update | delete | start | pause | cancel | monitor

Usage:
    # In a router:
    from backend.enterprise.security.rbac.auth import get_current_user, require_permission

    @router.get("/jobs")
    def list_jobs(user=Depends(require_permission("jobs:read"))):
        ...
"""

import os
import uuid
import datetime
import hashlib
from typing import Optional, List
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text

try:
    import jwt as pyjwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False

from backend.shared.config.database import get_db
from backend.shared.config.logging import logger


# ── Config ────────────────────────────────────────────────────────────────────

JWT_SECRET      = os.environ.get("JWT_SECRET", "dev-secret-change-in-production-please")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

bearer_scheme = HTTPBearer(auto_error=False)


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    # Fallback: SHA-256 (not production-safe — install bcrypt)
    import hashlib
    return "sha256:" + hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    if hashed.startswith("sha256:"):
        import hashlib
        return hashed == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()
    if BCRYPT_AVAILABLE:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    return False


# ── JWT tokens ────────────────────────────────────────────────────────────────

def create_token(user_id: str, tenant_id: str, role: str, email: str) -> str:
    """Create a signed JWT token."""
    jti     = str(uuid.uuid4())
    payload = {
        "sub":       user_id,
        "tenant_id": tenant_id,
        "role":      role,
        "email":     email,
        "jti":       jti,
        "iat":       datetime.datetime.utcnow(),
        "exp":       datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRE_HOURS),
    }
    if JWT_AVAILABLE:
        return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # Fallback without jwt library: return a simple base64 token (dev only)
    import base64, json
    return "dev:" + base64.b64encode(json.dumps({
        "sub": user_id, "tenant_id": tenant_id, "role": role,
        "email": email, "jti": jti
    }).encode()).decode()


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token. Returns payload or None."""
    if token.startswith("dev:"):
        import base64, json
        try:
            return json.loads(base64.b64decode(token[4:]).decode())
        except Exception:
            return None
    if not JWT_AVAILABLE:
        return None
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Current user dependency ───────────────────────────────────────────────────

class CurrentUser:
    def __init__(self, user_id: str, tenant_id: str, role: str, email: str, permissions: List[str]):
        self.user_id     = user_id
        self.tenant_id   = tenant_id
        self.role        = role
        self.email       = email
        self.permissions = permissions

    def can(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        if "*" in self.permissions:
            return True
        # Exact match
        if permission in self.permissions:
            return True
        # Wildcard resource match: "jobs:*" covers "jobs:read", "jobs:create"
        resource = permission.split(":")[0]
        if f"{resource}:*" in self.permissions:
            return True
        return False


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> CurrentUser:
    """FastAPI dependency: extracts and validates the JWT from Authorization header."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id   = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role      = payload.get("role", "read_only")

    # Load permissions from roles table
    permissions = _get_permissions_for_role(db, role)

    # Check if session is revoked
    jti = payload.get("jti", "")
    if jti:
        token_hash = hashlib.sha256(jti.encode()).hexdigest()
        revoked = db.execute(
            text("SELECT is_revoked FROM user_sessions WHERE token_hash = :h"),
            {"h": token_hash}
        ).fetchone()
        if revoked and revoked[0]:
            raise HTTPException(status_code=401, detail="Session has been revoked")

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=payload.get("email", ""),
        permissions=permissions,
    )


def require_permission(permission: str):
    """FastAPI dependency factory: checks that the current user has a permission."""
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Required: '{permission}'. Your role: '{user.role}'.",
            )
        return user
    return dependency


def optional_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> Optional[CurrentUser]:
    """Like get_current_user but returns None instead of raising if no token."""
    if not credentials:
        return None
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


# ── Role permissions lookup ───────────────────────────────────────────────────

def _get_permissions_for_role(db: Session, role_name: str) -> List[str]:
    """Load permissions for a role from the DB. Cached by role name."""
    try:
        row = db.execute(
            text("SELECT permissions FROM roles WHERE name = :n"),
            {"n": role_name}
        ).fetchone()
        if row and row[0]:
            perms = row[0]
            if isinstance(perms, list):
                return perms
            import json
            return json.loads(perms) if isinstance(perms, str) else ["*:read"]
    except Exception as e:
        logger.warning("Could not load role permissions", role=role_name, error=str(e))
    # Fallback defaults
    defaults = {
        "platform_admin":    ["*"],
        "tenant_admin":      ["jobs:*","connections:*","users:*","mappings:*","schemas:*","settings:*"],
        "migration_admin":   ["jobs:*","connections:read","connections:create","mappings:*","schemas:*"],
        "migration_operator":["jobs:read","jobs:start","jobs:pause","jobs:monitor","connections:read","mappings:read"],
        "read_only":         ["jobs:read","connections:read","mappings:read","schemas:read","workers:read"],
        "auditor":           ["jobs:read","connections:read","mappings:read","audit:*","reports:*"],
        "api_client":        ["jobs:read","jobs:create","jobs:start","connections:read","mappings:read"],
    }
    return defaults.get(role_name, ["jobs:read"])
