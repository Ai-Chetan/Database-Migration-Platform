"""
Progress Calculator
File: migration/backend/monitoring_service/app/services/progress_calculator.py

Converts raw DB data into human-readable progress metrics.
Used by all routers to build response payloads.
"""

from typing import Optional
from sqlalchemy.orm import Session

from backend.control_plane.app.models.migration import MigrationJob
from backend.monitoring_service.app.repositories.monitoring_repository import MonitoringRepository

repo = MonitoringRepository()


def compute_progress_pct(completed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((completed / total) * 100, 1)


def compute_elapsed_seconds(job: MigrationJob) -> Optional[float]:
    import datetime
    if not job.started_at:
        return None
    end = job.completed_at or datetime.datetime.utcnow()
    return (end - job.started_at).total_seconds()


def compute_eta_seconds(rows_total: int, rows_migrated: int, throughput_rps: int) -> Optional[int]:
    if throughput_rps <= 0 or rows_total <= 0:
        return None
    remaining = max(0, rows_total - rows_migrated)
    if remaining == 0:
        return 0
    return int(remaining / throughput_rps)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "Unknown"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    elif seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h {m}m"
    else:
        d, rem = divmod(seconds, 86400)
        h = rem // 3600
        return f"{d}d {h}h"


def format_throughput(rps: int) -> str:
    if rps <= 0:
        return "calculating..."
    if rps < 1000:
        return f"{rps} rows/sec"
    if rps < 1_000_000:
        return f"{rps:,} rows/sec"
    return f"{rps / 1_000_000:.1f}M rows/sec"


def build_job_detail(db: Session, job: MigrationJob) -> dict:
    """Builds the full JobDetail payload for GET /jobs/{id}"""
    chunk_stats  = repo.get_chunk_stats(db, str(job.id))
    rows_migrated = repo.get_rows_migrated(db, str(job.id))
    rows_total    = repo.get_total_rows(db, str(job.id))
    throughput    = repo.get_throughput_rps(db, str(job.id))
    elapsed       = compute_elapsed_seconds(job)
    eta           = compute_eta_seconds(rows_total, rows_migrated, throughput)

    chunks_total     = chunk_stats.get("total", 0)
    chunks_completed = chunk_stats.get("completed", 0)
    chunks_failed    = chunk_stats.get("failed", 0)

    source_engine = job.source_config.get("engine") if job.source_config else None
    target_engine = job.target_config.get("engine") if job.target_config else None

    return {
        "job_id":           str(job.id),
        "status":           job.status,
        "tenant_id":        job.tenant_id,
        "source_engine":    source_engine,
        "target_engine":    target_engine,
        "total_tables":     job.total_tables or 0,
        "total_chunks":     job.total_chunks or 0,
        "completed_chunks": job.completed_chunks or 0,
        "failed_chunks":    job.failed_chunks or 0,
        "progress_pct":     compute_progress_pct(chunks_completed, chunks_total),
        "rows_migrated":    rows_migrated,
        "rows_total":       rows_total,
        "throughput_rps":   throughput,
        "elapsed_seconds":  elapsed,
        "eta_seconds":      eta,
        "eta_human":        format_duration(eta),
        "elapsed_human":    format_duration(elapsed),
        "throughput_human": format_throughput(throughput),
        "started_at":       job.started_at,
        "completed_at":     job.completed_at,
        "created_at":       job.created_at,
    }
