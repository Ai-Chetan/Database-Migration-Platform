from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


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