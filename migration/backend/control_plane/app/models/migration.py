from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from backend.shared.config.database import Base
from sqlalchemy import BigInteger, Numeric, Text
import datetime
import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    BigInteger,
    Numeric,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from backend.shared.config.database import Base

import uuid
import datetime


class MigrationJob(Base):
    __tablename__ = "migration_jobs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    tenant_id = Column(
        String(100),
        nullable=False,
        default="local"
    )

    status = Column(
        String(30),
        nullable=False,
        default="pending"
    )

    source_config = Column(
        JSONB,
        nullable=False
    )

    target_config = Column(
        JSONB,
        nullable=False
    )

    total_tables = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    completed_chunks = Column(Integer, default=0)
    failed_chunks = Column(Integer, default=0)

    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow
    )

    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    last_error = Column(Text)

class MigrationTable(Base):
    __tablename__ = "migration_tables"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id"),
        nullable=False
    )

    table_name = Column(
        String(255),
        nullable=False
    )

    primary_key_column = Column(
        String(255)
    )

    total_rows = Column(BigInteger)

    total_chunks = Column(Integer, default=0)
    completed_chunks = Column(Integer, default=0)
    failed_chunks = Column(Integer, default=0)

    status = Column(
        String(30),
        default="pending"
    )

    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )
    

class MigrationChunk(Base):
    __tablename__ = "migration_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True)

    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("migration_jobs.id")
    )

    table_id = Column(
        UUID(as_uuid=True),
        ForeignKey("migration_tables.id")
    )

    table_name = Column(String(255), nullable=False)

    pk_start = Column(BigInteger, nullable=False)

    pk_end = Column(BigInteger, nullable=False)

    status = Column(String(30), default="pending")

    retry_count = Column(Integer, default=0)

    max_retries = Column(Integer, default=3)

    rows_processed = Column(BigInteger, default=0)

    checksum = Column(String(128))

    duration_ms = Column(BigInteger)

    started_at = Column(DateTime)

    completed_at = Column(DateTime)

    last_heartbeat = Column(DateTime)

    last_error = Column(Text)

    created_at = Column(DateTime)

    worker_id = Column(String(100))

    next_retry_at = Column(DateTime)

    source_row_count = Column(BigInteger)

    target_row_count = Column(BigInteger)

    validation_status = Column(String(30), default="pending")

    throughput_rows_per_sec = Column(Numeric(15, 2))

    throughput_mb_per_sec = Column(Numeric(10, 2))

    memory_peak_mb = Column(Integer)

    insert_latency_ms = Column(Integer)

    batch_size_used = Column(Integer, default=5000)

    bulk_insert_method = Column(String(50), default="standard")

class TableMapping(Base):
    __tablename__ = "table_mappings"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    table_id = Column(
        UUID(as_uuid=True),
        ForeignKey("migration_tables.id")
    )

    mapping_type = Column(String)

    source = Column(String)

    target = Column(String)

    config = Column(JSON)