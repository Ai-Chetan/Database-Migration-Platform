from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
import datetime

Base = declarative_base()

class MigrationJob(Base):
    __tablename__ = "migration_jobs"
    id = Column(String, primary_key=True)
    status = Column(String, default="PENDING")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    config = Column(JSON, nullable=True)

class MigrationTable(Base):
    __tablename__ = "migration_tables"
    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("migration_jobs.id"))
    source_table_name = Column(String, nullable=False)
    target_table_name = Column(String, nullable=False)
    status = Column(String, default="PENDING")
    
class MigrationChunk(Base):
    __tablename__ = "migration_chunks"
    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("migration_jobs.id"))
    table_id = Column(String, ForeignKey("migration_tables.id"))
    status = Column(String, default="PENDING")
    min_pk = Column(String, nullable=True)
    max_pk = Column(String, nullable=True)
    offset = Column(Integer, nullable=True)
    limit = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class Mapping(Base):
    __tablename__ = "mappings"
    id = Column(String, primary_key=True)
    table_id = Column(String, ForeignKey("migration_tables.id"))
    mapping_type = Column(String)  # TABLE or COLUMN
    source = Column(String)
    target = Column(String)
    config = Column(JSON, nullable=True)
