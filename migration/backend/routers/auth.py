"""
Auth Router
File: migration/backend/routers/auth.py

Endpoints:
    POST /auth/login              → authenticate, returns JWT + user
    POST /auth/logout              → revoke current session
    GET  /auth/me                  → current user info
    POST /auth/change-password     → change own password
    POST /auth/forgot-password     → request password reset (stub — logs token, real email TODO)
    POST /auth/reset-password      → complete password reset with token
"""

import datetime
import hashlib
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

from backend.shared.config.database import get_db
from backend.shared.auth.auth_service import AuthService, AuthError
from backend.shared.middleware.jwt_middleware import get_current_user
from backend.shared.config.logging import logger

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email:    str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:      str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token:        str
    new_password: str


@router.post("/login", summary="Authenticate and receive a JWT access token")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Authenticates a user by email and password.

    Returns:
    {
      "access_token": "eyJhbGc...",
      "token_type": "bearer",
      "expires_at": "2026-07-19T10:00:00",
      "user": {
        "id": "...", "email": "...", "name": "...", "role": "migration_admin",
        "tenant_id": "local", "force_password_change": false,
        "permissions": ["create:job", "start:job", ...]
      }
    }

    On repeated failures (5 by default), the account is locked for 15 minutes.
    Default admin account: admin@local / ChangeMe123!
    (force_password_change=true — the frontend must redirect to change password)
    """
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")

    try:
        result = AuthService.authenticate(
            db=db, email=req.email, password=req.password,
            ip_address=ip_address, user_agent=user_agent,
        )
        return result
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/logout", summary="Revoke the current session")
def logout(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revokes the current JWT session. The token itself remains structurally valid
    but is rejected on future requests because the session record is marked revoked."""
    if user.get("jti"):
        AuthService.revoke_session(db, user["jti"], reason="logout")
    return {"message": "Logged out successfully."}


@router.get("/me", summary="Get current authenticated user")
def get_me(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns full current user details including permissions list."""
    full_user = AuthService.get_user_by_id(db, user["id"])
    if not full_user:
        raise HTTPException(status_code=404, detail="User not found.")
    return full_user


@router.post("/change-password", summary="Change your own password")
def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Changes the current user's password. Requires the current password for verification.
    New password must be at least 8 characters with 1 uppercase letter and 1 number.
    All other active sessions for this user are revoked after a successful change.
    """
    try:
        AuthService.change_password(db, user["id"], req.current_password, req.new_password)
        return {"message": "Password changed successfully. Other sessions have been signed out."}
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/forgot-password", summary="Request a password reset link")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Generates a password reset token valid for 1 hour.

    NOTE: This endpoint always returns success regardless of whether the email
    exists, to avoid leaking which emails are registered. In production, wire this
    to the Email Notifier (Part 8) to actually send the reset link. For now the
    raw token is logged server-side so an administrator can retrieve it manually
    during initial setup / testing.
    """
    email = req.email.strip().lower()
    row = db.execute(
        text("SELECT id FROM users WHERE LOWER(email)=:email AND is_active=TRUE"),
        {"email": email}
    ).fetchone()

    if row:
        raw_token   = secrets.token_urlsafe(32)
        token_hash  = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at  = datetime.datetime.utcnow() + datetime.timedelta(hours=1)

        db.execute(
            text("""
                INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at)
                VALUES (gen_random_uuid(), :uid, :hash, :exp, :now)
            """),
            {"uid": row[0], "hash": token_hash, "exp": expires_at,
             "now": datetime.datetime.utcnow()}
        )
        db.commit()

        logger.info("Password reset token generated (send via email in production)",
                    email=email, token=raw_token, expires_at=expires_at.isoformat())

    return {"message": "If an account exists for this email, a reset link has been sent."}


@router.post("/reset-password", summary="Complete password reset with a token")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Completes the password reset flow using the token from forgot-password."""
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()

    row = db.execute(
        text("""
            SELECT id, user_id FROM password_reset_tokens
            WHERE token_hash=:hash AND used_at IS NULL AND expires_at > :now
        """),
        {"hash": token_hash, "now": datetime.datetime.utcnow()}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")

    error = AuthService.validate_password_strength(req.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)

    new_hash = AuthService.hash_password(req.new_password)
    db.execute(
        text("""
            UPDATE users SET password_hash=:h, force_password_change=FALSE,
                failed_login_attempts=0, locked_until=NULL, updated_at=:now
            WHERE id=:id
        """),
        {"h": new_hash, "now": datetime.datetime.utcnow(), "id": row.user_id}
    )
    db.execute(
        text("UPDATE password_reset_tokens SET used_at=:now WHERE id=:id"),
        {"now": datetime.datetime.utcnow(), "id": row.id}
    )
    db.commit()
    AuthService.revoke_all_sessions(db, str(row.user_id), reason="password_reset")

    return {"message": "Password reset successfully. You can now sign in with your new password."}
