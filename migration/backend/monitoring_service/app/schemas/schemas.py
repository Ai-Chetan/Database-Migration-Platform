"""
Monitoring Service — Pydantic Schemas
File: migration/backend/monitoring_service/app/schemas/schemas.py

All request/response shapes for the monitoring API.
"""

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


# ── Job Schemas ───────────────────────────────────────────────────────────────

class JobSummary(BaseModel):
    job_id: str
    status: str
    tenant_id: str
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    progress_pct: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobDetail(BaseModel):
    job_id: str
    status: str
    tenant_id: str
    source_engine: Optional[str]
    target_engine: Optional[str]
    total_tables: int
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    progress_pct: float
    rows_migrated: int
    rows_total: int
    throughput_rps: int
    elapsed_seconds: Optional[float]
    eta_seconds: Optional[int]
    eta_human: str
    elapsed_human: str
    throughput_human: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Chunk Schemas ─────────────────────────────────────────────────────────────

class ChunkSummary(BaseModel):
    chunk_id: str
    table_name: str
    status: str
    pk_start: int
    pk_end: int
    rows_processed: int
    worker_id: Optional[str]
    retry_count: int
    duration_ms: Optional[int]
    throughput_rps: Optional[float]
    validation_status: Optional[str]
    checksum: Optional[str]
    last_error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ChunkListResponse(BaseModel):
    job_id: str
    total: int
    chunks: List[ChunkSummary]


# ── Worker Schemas ────────────────────────────────────────────────────────────

class WorkerStatus(BaseModel):
    worker_name: str
    worker_status: str
    current_chunk_id: Optional[str]
    hostname: Optional[str]
    cpu_usage: Optional[float]
    memory_usage: Optional[float]
    last_heartbeat: Optional[datetime]
    is_stale: bool   # True if heartbeat is older than 15 minutes

    class Config:
        from_attributes = True


# ── Metrics Schemas ───────────────────────────────────────────────────────────

class JobMetrics(BaseModel):
    job_id: str
    rows_processed: int
    rows_per_second: int
    eta_seconds: Optional[int]
    eta_minutes: Optional[float]
    eta_human: str
    chunks_completed: int
    chunks_total: int
    chunks_failed: int
    progress_pct: float
    elapsed_seconds: Optional[float]
    elapsed_human: str
    throughput_human: str

    class Config:
        from_attributes = True


class TableProgress(BaseModel):
    table_id: str
    table_name: str
    status: str
    completion_pct: float
    total_rows: int
    total_chunks: int
    completed_chunks: int
    failed_chunks: int

    class Config:
        from_attributes = True


class PlatformSummary(BaseModel):
    active_jobs: int
    completed_jobs: int
    failed_jobs: int
    total_workers: int
    active_workers: int
    redis_queue_depth: int
    redis_retry_queue_depth: int
    total_rows_migrated: int

    class Config:
        from_attributes = True
