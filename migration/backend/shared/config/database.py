from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError
from backend.shared.config.settings import settings
from backend.shared.exceptions.database import DatabaseException
from urllib.parse import quote_plus

def get_database_url() -> str:
    password = quote_plus(settings.postgres_password)

    return (
        f"postgresql://{settings.postgres_user}:"
        f"{password}@"
        f"{settings.postgres_host}:"
        f"{settings.postgres_port}/"
        f"{settings.postgres_db}"
    )

engine = create_engine(
    get_database_url(),
    pool_size=20,
    max_overflow=50,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.debug
)

SessionFactory = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False
)

SessionLocal = SessionFactory

Base = declarative_base()

def get_db():
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()

def check_database_connection() -> bool:
    try:
        with engine.connect() as conn:
            # Simple ping
            conn.execute("SELECT 1")
        return True
    except SQLAlchemyError as e:
        raise DatabaseException(code="DB_CONNECTION_FAILED", message="Failed to connect to the database", details={"error": str(e)})


