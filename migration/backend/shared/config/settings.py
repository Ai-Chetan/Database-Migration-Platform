from dotenv import load_dotenv, find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

# ── Load .env into the real process environment ─────────────────────────────
# CHANGE: Settings(BaseSettings) below reads .env for its OWN typed fields,
# but pydantic-settings does NOT copy those values into os.environ. Several
# modules read secrets straight from os.environ (connection_manager.py's
# MIGRATION_ENCRYPTION_KEY, masking_strategies.py, shared/auth/auth_email.py's
# SMTP_* vars) - none of them ever saw values from .env, because nothing in
# the codebase called load_dotenv(). That's the real reason the logs showed
# "MIGRATION_ENCRYPTION_KEY not set — using ephemeral key": the key WAS in
# backend/enterprise/.env, but that file was never loaded by the process
# that's actually running (backend.main:app, started from the migration/
# directory) - and even the root migration/.env wasn't being loaded either.
# find_dotenv() walks up from the current working directory, so this picks
# up migration/.env whether the app is started from migration/ or a
# subdirectory. This file is imported early enough (settings.py is one of
# the first backend modules everything else imports) that os.environ is
# populated before any module-level os.environ.get(...) call runs.
load_dotenv(find_dotenv(filename=".env", usecwd=True))


class Settings(BaseSettings):
    # Application
    app_name: str
    app_env: str
    app_version: str
    debug: bool

    # Database
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    # Redis
    redis_host: str
    redis_port: int
    redis_password: Optional[str] = None

    # Security
    jwt_secret: str
    jwt_algorithm: str
    jwt_expiration_minutes: int
    migration_encryption_key: Optional[str] = None

    # Email (all optional - falls back to console logging if unset)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_addr: str = "no-reply@migrationplatform.local"
    smtp_use_tls: bool = True
    app_base_url: str = "http://localhost:5173"

    # Monitoring
    prometheus_enabled: bool
    log_level: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()