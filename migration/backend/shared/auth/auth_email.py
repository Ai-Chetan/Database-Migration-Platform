"""
Auth Email Helper
File: migration/backend/shared/auth/auth_email.py

Wires password-reset and welcome emails to the real EmailNotifier plugin
(Part 8: backend/plugins/notifiers/notifier_plugins.py) instead of just
logging to console.

Environment variables used (all optional - if unset, falls back to
console logging exactly as before, so nothing breaks if email isn't
configured yet):

    SMTP_HOST
    SMTP_PORT           (default 587)
    SMTP_USER
    SMTP_PASSWORD
    SMTP_FROM_ADDR       (default "no-reply@migrationplatform.local")
    SMTP_USE_TLS         (default "true")
    APP_BASE_URL         (default "http://localhost:5173")

Usage:
    from backend.shared.auth.auth_email import send_password_reset_email, send_welcome_email

    send_password_reset_email(db, to_email="user@company.com", reset_token="abc123...")
    send_welcome_email(db, to_email="user@company.com", name="Jane Doe", temp_password="Xk9$mP2q")
"""

import os
from typing import Optional
from sqlalchemy.orm import Session

from backend.shared.config.logging import logger

SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_ADDR = os.environ.get("SMTP_FROM_ADDR", "no-reply@migrationplatform.local")
SMTP_USE_TLS   = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
APP_BASE_URL   = os.environ.get("APP_BASE_URL", "http://localhost:5173")


def _is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def _send(to_email: str, subject: str, body: str) -> bool:
    if not _is_configured():
        logger.info(
            "SMTP not configured - email not sent (dev fallback: logging content instead)",
            to=to_email, subject=subject, body_preview=body[:200],
        )
        return False

    try:
        from backend.plugins.notifiers.notifier_plugins import EmailNotifier

        notifier = EmailNotifier({
            "smtp_host":     SMTP_HOST,
            "smtp_port":     SMTP_PORT,
            "smtp_user":     SMTP_USER,
            "smtp_password": SMTP_PASSWORD,
            "from_addr":     SMTP_FROM_ADDR,
            "to_addrs":      [to_email],
            "use_tls":       SMTP_USE_TLS,
        })

        synthetic_event = {
            "event_type":      "auth.email",
            "resource_id":     to_email,
            "source_service":  "auth",
            "payload":         {"subject_override": subject, "body_override": body},
        }
        success = notifier.send(synthetic_event)
        if success:
            logger.info("Email sent", to=to_email, subject=subject)
        else:
            logger.warning("Email send failed", to=to_email, subject=subject)
        return success

    except Exception as e:
        logger.warning("Email send raised an exception", to=to_email, error=str(e))
        return False


def send_password_reset_email(db: Session, to_email: str, reset_token: str) -> bool:
    reset_link = f"{APP_BASE_URL}/reset-password?token={reset_token}"
    subject = "Reset your Migration Platform password"
    body = (
        f"A password reset was requested for your account.\n\n"
        f"Click the link below to set a new password. This link expires in 1 hour.\n\n"
        f"{reset_link}\n\n"
        f"If you did not request this, you can safely ignore this email, "
        f"your password will remain unchanged."
    )
    return _send(to_email, subject, body)


def send_welcome_email(
    db: Session, to_email: str, name: str, temp_password: Optional[str] = None
) -> bool:
    login_link = f"{APP_BASE_URL}/login"
    subject = "Welcome to Migration Platform"

    if temp_password:
        body = (
            f"Hi {name},\n\n"
            f"An account has been created for you on Migration Platform.\n\n"
            f"Email:               {to_email}\n"
            f"Temporary password:  {temp_password}\n\n"
            f"Sign in here: {login_link}\n\n"
            f"You will be asked to set a new password on your first login."
        )
    else:
        body = (
            f"Hi {name},\n\n"
            f"An account has been created for you on Migration Platform.\n\n"
            f"Sign in here: {login_link}\n\n"
            f"If you don't know your password, use the 'Forgot password' link on the sign-in page."
        )

    return _send(to_email, subject, body)


def send_password_changed_notice(db: Session, to_email: str, name: str) -> bool:
    subject = "Your Migration Platform password was changed"
    body = (
        f"Hi {name},\n\n"
        f"This is a confirmation that your password was just changed. "
        f"All other active sessions on your account have been signed out as a precaution.\n\n"
        f"If you did not make this change, contact your administrator immediately."
    )
    return _send(to_email, subject, body)


# =============================================================================
# INTEGRATION NOTES - two small edits to wire this into the existing files
# from the previous auth batch. Not new files, just two call-site additions.
# =============================================================================
#
# 1. In migration/backend/routers/auth.py, inside forgot_password():
#    Replace the existing:
#        logger.info("Password reset token generated (send via email in production)", ...)
#    With:
#        from backend.shared.auth.auth_email import send_password_reset_email
#        send_password_reset_email(db, to_email=email, reset_token=raw_token)
#
# 2. In migration/backend/routers/users.py, inside create_user(), after commit:
#    Replace the existing:
#        logger.info("Welcome email would be sent here", ...)
#    With:
#        if req.send_welcome_email:
#            from backend.shared.auth.auth_email import send_welcome_email
#            send_welcome_email(db, to_email=req.email, name=req.name,
#                                temp_password=generated_password)
#
# Both are drop-in replacements - no other logic changes required.
# =============================================================================
