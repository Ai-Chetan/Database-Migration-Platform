"""
JWT Middleware / Auth Dependencies
File: migration/backend/shared/middleware/jwt_middleware.py

FastAPI dependencies for protecting routes with JWT authentication
and role-based access control.

Usage in any router:

    from backend.shared.middleware.jwt_middleware import get_current_user, require_role

    @router.get("/jobs")
    def list_jobs(user: dict = Depends(get_current_user)):
        # user is guaranteed valid here
        ...

    @router.post("/ops/maintenance/enable")
    def enable_maintenance(user: dict = Depends(require_role("platform_admin", "tenant_admin"))):
        # only these roles can reach this line
        ...

    @router.post("/ops/workers/{id}/pause")
    def pause_worker(user: dict = Depends(require_permission("pause:job"))):
        ...

Design notes:
    - get_current_user() decodes the JWT, verifies the session is not revoked,
      and returns the user dict. Raises 401 if invalid.
    - require_role() wraps get_current_user() and additionally checks role membership.
    - require_permission() checks against the role_definitions.permissions array,
      with '*' meaning all permissions (platform_admin).
    - All three read the token from the standard `Authorization: Bearer <token>` header.
"""

from typing import Optional, List
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.shared.auth.auth_service import AuthService, AuthError
from backend.shared.config.logging import logger

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """
    Core auth dependency. Decodes JWT, verifies session validity, returns user claims.
    Use this on every protected route.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Include 'Authorization: Bearer <token>' header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = AuthService.decode_token(token)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message,
                            headers={"WWW-Authenticate": "Bearer"})

    jti = payload.get("jti")
    if jti and not AuthService.is_session_valid(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id":        payload.get("sub"),
        "email":     payload.get("email"),
        "name":      payload.get("name"),
        "role":      payload.get("role"),
        "tenant_id": payload.get("tenant_id", "local"),
        "jti":       jti,
    }


def require_role(*allowed_roles: str):
    """
    Dependency factory. Returns a dependency that requires the user's role
    to be one of allowed_roles. platform_admin always passes (superuser).

    Usage: Depends(require_role("platform_admin", "tenant_admin"))
    """
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] == "platform_admin":
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of the following roles: {', '.join(allowed_roles)}."
            )
        return user
    return _check


def require_permission(*permissions: str):
    """
    Dependency factory. Returns a dependency that requires the user's role
    to have at least one of the given permissions (from role_definitions table).
    platform_admin (permissions=['*']) always passes.

    Usage: Depends(require_permission("cancel:job"))
    """
    async def _check(
        user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> dict:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT permissions FROM role_definitions WHERE role_name=:r"),
            {"r": user["role"]}
        ).fetchone()

        user_perms = list(row[0]) if row and row[0] else []

        if "*" in user_perms:
            return user

        if not any(p in user_perms for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of the following permissions: {', '.join(permissions)}."
            )
        return user
    return _check


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[dict]:
    """
    Like get_current_user but returns None instead of raising if not authenticated.
    Use on routes that behave differently for logged-in vs anonymous users
    (rare in this platform, but available).
    """
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None
