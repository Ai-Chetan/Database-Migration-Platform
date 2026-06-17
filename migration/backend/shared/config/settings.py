from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Application
    app_name: str = "Migration Platform"
    app_env: str = "development"
    app_version: str = "1.0.0"
    debug: bool = True

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "migration_db"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None

    # Security
    jwt_secret: str = "supersecretkey"  # change in production
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60

    # Monitoring
    prometheus_enabled: bool = True
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

settings = Settings()
