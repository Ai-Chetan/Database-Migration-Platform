"""
Auth Service
File: migration/backend/shared/auth/auth_service.py

Core authentication logic: password hashing, JWT creation/verification,
session management, login attempt throttling.

Uses:
    bcrypt      → password hashing (via passlib)
    python-jose → JWT encode/decode

Install:
    pip install "python-jose[cryptography]" "passlib[bcrypt]"

Environment variables required:
    JWT_SECRET          → long random string, generate with:
                           python -c "import secrets; print(secrets.token_urlsafe(64))"
    JWT_ALGORITHM        → default HS256
    JWT_EXPIRE_HOURS     → default 24
    MAX_LOGIN_ATTEMPTS   → default 5
    LOCKOUT_MINUTES      → default 15
"""

import os
import uuid
import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger

try:
    from passlib.context import CryptContext
    _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except ImportError:
    _pwd_context = None

try:
    from jose import jwt, JWTError
except ImportError:
    jwt = None
    JWTError = Exception


JWT_SECRET        = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM      = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS    = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))
MAX_LOGIN_ATTEMPTS  = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES     = int(os.environ.get("LOCKOUT_MINUTES", "15"))

if not JWT_SECRET:
    logger.warning(
        "JWT_SECRET is not set! Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(64))\" "
        "and set it in your .env file. Using an insecure default for now."
    )
    JWT_SECRET = "INSECURE-DEV-ONLY-CHANGE-ME-" + uuid.uuid4().hex


class AuthError(Exception):
    """Raised for any authentication failure. Message is safe to show to the user."""
    def __init__(self, message: str, status_code: int = 401):
        self.message     = message
        self.status_code = status_code
        super().__init__(message)


class AuthService:

    # ── Password hashing ──────────────────────────────────────────────────────

    @staticmethod
    def hash_password(plain_password: str) -> str:
        if _pwd_context is None:
            raise RuntimeError("passlib not installed. Run: pip install \"passlib[bcrypt]\"")
        return _pwd_context.hash(plain_password)

    @staticmethod
    def verify_password(plain_password: str, password_hash: str) -> bool:
        if _pwd_context is None:
            raise RuntimeError("passlib not installed. Run: pip install \"passlib[bcrypt]\"")
        try:
            return _pwd_context.verify(plain_password, password_hash)
        except Exception:
            return False

    @staticmethod
    def validate_password_strength(password: str) -> Optional[str]:
        """Returns an error message if weak, None if acceptable."""
        if len(password) < 8:
            return "Password must be at least 8 characters long."
        if not any(c.isupper() for c in password):
            return "Password must contain at least one uppercase letter."
        if not any(c.isdigit() for c in password):
            return "Password must contain at least one number."
        return None

    # ── JWT ────────────────────────────────────────────────────────────────────

    @classmethod
    def create_access_token(cls, user: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a JWT for a user. Returns {token, jti, expires_at}.
        Claims: sub (user id), email, role, tenant_id, jti (unique token id), exp
        """
        if jwt is None:
            raise RuntimeError("python-jose not installed. Run: pip install \"python-jose[cryptography]\"")

        jti        = str(uuid.uuid4())
        now        = datetime.datetime.utcnow()
        expires_at = now + datetime.timedelta(hours=JWT_EXPIRE_HOURS)

        claims = {
            "sub":       str(user["id"]),
            "email":     user["email"],
            "name":      user.get("name", ""),
            "role":      user["role"],
            "tenant_id": user.get("tenant_id", "local"),
            "jti":       jti,
            "iat":       now,
            "exp":       expires_at,
        }
        token = jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"token": token, "jti": jti, "expires_at": expires_at}

    @classmethod
    def decode_token(cls, token: str) -> Dict[str, Any]:
        """Decode and verify a JWT. Raises AuthError if invalid or expired."""
        if jwt is None:
            raise RuntimeError("python-jose not installed.")
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except JWTError:
            raise AuthError("Invalid or expired session. Please sign in again.", 401)

    # ── Login flow ─────────────────────────────────────────────────────────────

    @classmethod
    def authenticate(
        cls,
        db:         Session,
        email:      str,
        password:   str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Full login flow: verify credentials, check lockout, issue token, record session.
        Returns {access_token, token_type, user}.
        Raises AuthError with a safe message on any failure.
        """
        email = email.strip().lower()

        row = db.execute(
            text("""
                SELECT id, tenant_id, email, name, password_hash, role, is_active,
                       force_password_change, failed_login_attempts, locked_until
                FROM users WHERE LOWER(email) = :email
            """),
            {"email": email}
        ).fetchone()

        if not row:
            # Do not reveal whether the email exists
            raise AuthError("Invalid email or password.", 401)

        user = dict(row._mapping)

        # Check lockout
        locked_until = user.get("locked_until")
        if locked_until and locked_until > datetime.datetime.utcnow():
            minutes_left = int((locked_until - datetime.datetime.utcnow()).total_seconds() / 60) + 1
            raise AuthError(
                f"Account temporarily locked due to too many failed attempts. "
                f"Try again in {minutes_left} minute(s).", 423
            )

        if not user["is_active"]:
            raise AuthError("This account has been deactivated. Contact your administrator.", 403)

        # Verify password
        if not cls.verify_password(password, user["password_hash"]):
            cls._record_failed_login(db, user["id"], user.get("failed_login_attempts", 0))
            raise AuthError("Invalid email or password.", 401)

        # Success — reset failed attempts, update last login
        db.execute(
            text("""
                UPDATE users SET
                    failed_login_attempts = 0, locked_until = NULL,
                    last_login = :now, updated_at = :now
                WHERE id = :id
            """),
            {"now": datetime.datetime.utcnow(), "id": user["id"]}
        )

        # Issue token
        token_data = cls.create_access_token(user)

        # Record session
        db.execute(
            text("""
                INSERT INTO user_sessions
                    (id, user_id, token_jti, ip_address, user_agent, issued_at, expires_at)
                VALUES (gen_random_uuid(), :uid, :jti, :ip, :ua, :now, :exp)
            """),
            {
                "uid": user["id"], "jti": token_data["jti"],
                "ip": ip_address, "ua": user_agent,
                "now": datetime.datetime.utcnow(), "exp": token_data["expires_at"],
            }
        )
        db.commit()

        logger.info("User authenticated", email=email, role=user["role"])

        return {
            "access_token": token_data["token"],
            "token_type":   "bearer",
            "expires_at":   token_data["expires_at"].isoformat(),
            "user": {
                "id":                    str(user["id"]),
                "email":                 user["email"],
                "name":                  user["name"],
                "role":                  user["role"],
                "tenant_id":             user["tenant_id"],
                "force_password_change": user["force_password_change"],
                "permissions":           cls._get_permissions(db, user["role"]),
            },
        }

    @classmethod
    def _record_failed_login(cls, db: Session, user_id: str, current_attempts: int) -> None:
        new_attempts = current_attempts + 1
        locked_until = None
        if new_attempts >= MAX_LOGIN_ATTEMPTS:
            locked_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning("Account locked due to failed login attempts", user_id=user_id)

        db.execute(
            text("""
                UPDATE users SET failed_login_attempts=:n, locked_until=:lu, updated_at=:now
                WHERE id=:id
            """),
            {"n": new_attempts, "lu": locked_until,
             "now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()

    # ── Logout / session revocation ───────────────────────────────────────────

    @classmethod
    def revoke_session(cls, db: Session, jti: str, reason: str = "logout") -> None:
        db.execute(
            text("""
                UPDATE user_sessions SET revoked_at=:now, revoked_reason=:reason
                WHERE token_jti=:jti AND revoked_at IS NULL
            """),
            {"now": datetime.datetime.utcnow(), "reason": reason, "jti": jti}
        )
        db.commit()

    @classmethod
    def revoke_all_sessions(cls, db: Session, user_id: str, reason: str = "admin_revoked") -> int:
        result = db.execute(
            text("""
                UPDATE user_sessions SET revoked_at=:now, revoked_reason=:reason
                WHERE user_id=:uid AND revoked_at IS NULL
            """),
            {"now": datetime.datetime.utcnow(), "reason": reason, "uid": user_id}
        )
        db.commit()
        return result.rowcount

    @classmethod
    def is_session_valid(cls, db: Session, jti: str) -> bool:
        row = db.execute(
            text("""
                SELECT 1 FROM user_sessions
                WHERE token_jti=:jti AND revoked_at IS NULL AND expires_at > :now
            """),
            {"jti": jti, "now": datetime.datetime.utcnow()}
        ).fetchone()
        return row is not None

    # ── Password management ───────────────────────────────────────────────────

    @classmethod
    def change_password(
        cls, db: Session, user_id: str, current_password: str, new_password: str
    ) -> None:
        row = db.execute(
            text("SELECT password_hash FROM users WHERE id=:id"), {"id": user_id}
        ).fetchone()
        if not row:
            raise AuthError("User not found.", 404)

        if not cls.verify_password(current_password, row[0]):
            raise AuthError("Current password is incorrect.", 400)

        error = cls.validate_password_strength(new_password)
        if error:
            raise AuthError(error, 400)

        new_hash = cls.hash_password(new_password)
        db.execute(
            text("""
                UPDATE users SET password_hash=:h, force_password_change=FALSE, updated_at=:now
                WHERE id=:id
            """),
            {"h": new_hash, "now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()
        # Revoke all other sessions for security
        cls.revoke_all_sessions(db, user_id, reason="password_changed")
        logger.info("Password changed", user_id=user_id)

    @classmethod
    def admin_reset_password(
        cls, db: Session, user_id: str, new_password: str, force_change: bool = True
    ) -> None:
        error = cls.validate_password_strength(new_password)
        if error:
            raise AuthError(error, 400)

        new_hash = cls.hash_password(new_password)
        db.execute(
            text("""
                UPDATE users SET password_hash=:h, force_password_change=:fc,
                    failed_login_attempts=0, locked_until=NULL, updated_at=:now
                WHERE id=:id
            """),
            {"h": new_hash, "fc": force_change,
             "now": datetime.datetime.utcnow(), "id": user_id}
        )
        db.commit()
        cls.revoke_all_sessions(db, user_id, reason="password_reset_by_admin")
        logger.info("Password reset by admin", user_id=user_id)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @classmethod
    def _get_permissions(cls, db: Session, role: str) -> List[str]:
        row = db.execute(
            text("SELECT permissions FROM role_definitions WHERE role_name=:r"),
            {"r": role}
        ).fetchone()
        return list(row[0]) if row and row[0] else []

    @classmethod
    def get_user_by_id(cls, db: Session, user_id: str) -> Optional[Dict[str, Any]]:
        row = db.execute(
            text("""
                SELECT id, tenant_id, email, name, role, is_active,
                       force_password_change, last_login, created_at
                FROM users WHERE id=:id
            """),
            {"id": user_id}
        ).fetchone()
        if not row:
            return None
        d = dict(row._mapping)
        d["id"] = str(d["id"])
        for k in ("last_login", "created_at"):
            if d.get(k):
                d[k] = d[k].isoformat()
        d["permissions"] = cls._get_permissions(db, d["role"])
        return d
