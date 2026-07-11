"""
Monitoring Repository
File: migration/backend/monitoring_service/app/repositories/monitoring_repository.py

All database queries for the monitoring service.
Read-only — never writes to the DB.

Uses your exact model column names from migration.py:
    MigrationJob:   id, status, tenant_id, total_chunks, completed_chunks,
                    failed_chunks, total_tables, started_at, completed_at,
                    created_at, source_config, target_config
    MigrationChunk: id, job_id, table_id, table_name, status, pk_start, pk_end,
                    rows_processed, worker_id, retry_count, duration_ms,
                    throughput_rows_per_sec, validation_status, checksum,
                    last_error, started_at, completed_at, last_heartbeat
    MigrationTable: id, job_id, table_name, status, total_rows, total_chunks,
                    completed_chunks, failed_chunks
"""

import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from backend.control_plane.app.models.migration import (
    MigrationJob, MigrationChunk, MigrationTable
)


STALE_THRESHOLD_MINUTES = 15


class MonitoringRepository:

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def get_all_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).order_by(MigrationJob.created_at.desc()).all()

    def get_job_by_id(self, db: Session, job_id: str) -> Optional[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.id == job_id).first()

    def get_active_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(
            MigrationJob.status.in_(["running", "pending", "planning"])
        ).all()

    def count_jobs_by_status(self, db: Session) -> Dict[str, int]:
        rows = db.query(
            MigrationJob.status,
            func.count(MigrationJob.id).label("count")
        ).group_by(MigrationJob.status).all()
        return {row.status: row.count for row in rows}

    # ── Chunks ────────────────────────────────────────────────────────────────

    def get_chunks_for_job(self, db: Session, job_id: str) -> List[MigrationChunk]:
        return db.query(MigrationChunk).filter(
            MigrationChunk.job_id == job_id
        ).order_by(MigrationChunk.pk_start).all()

    def get_chunk_stats(self, db: Session, job_id: str) -> Dict[str, int]:
        """Returns count of chunks grouped by status for a job."""
        rows = db.query(
            MigrationChunk.status,
            func.count(MigrationChunk.id).label("count")
        ).filter(
            MigrationChunk.job_id == job_id
        ).group_by(MigrationChunk.status).all()

        stats = {row.status: row.count for row in rows}
        stats["total"] = sum(stats.values())
        return stats

    def get_rows_migrated(self, db: Session, job_id: str) -> int:
        """Total rows_processed across all COMPLETED chunks for a job."""
        result = db.query(
            func.sum(MigrationChunk.rows_processed)
        ).filter(
            MigrationChunk.job_id == job_id,
            MigrationChunk.status == "completed"
        ).scalar()
        return int(result or 0)

    def get_total_rows(self, db: Session, job_id: str) -> int:
        """Sum of total_rows across all tables in a job."""
        result = db.query(
            func.sum(MigrationTable.total_rows)
        ).filter(
            MigrationTable.job_id == job_id
        ).scalar()
        return int(result or 0)

    def get_throughput_rps(self, db: Session, job_id: str) -> int:
        """
        Rolling average throughput from the last 10 completed chunks.
        Uses duration_ms and rows_processed from migration_chunks.
        """
        recent = db.query(MigrationChunk).filter(
            MigrationChunk.job_id == job_id,
            MigrationChunk.status == "completed",
            MigrationChunk.rows_processed > 0,
            MigrationChunk.duration_ms > 0
        ).order_by(MigrationChunk.completed_at.desc()).limit(10).all()

        if not recent:
            return 0

        rates = []
        for chunk in recent:
            if chunk.duration_ms and chunk.duration_ms > 0:
                rate = chunk.rows_processed / (chunk.duration_ms / 1000.0)
                rates.append(rate)

        return int(sum(rates) / len(rates)) if rates else 0

    # ── Tables ────────────────────────────────────────────────────────────────

    def get_tables_for_job(self, db: Session, job_id: str) -> List[MigrationTable]:
        return db.query(MigrationTable).filter(
            MigrationTable.job_id == job_id
        ).all()

    # ── Workers ───────────────────────────────────────────────────────────────

    def get_all_workers(self, db: Session) -> List[Dict[str, Any]]:
        """
        Returns all rows from worker_heartbeats.
        Uses raw SQL because worker_heartbeats has no SQLAlchemy model.
        """
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=STALE_THRESHOLD_MINUTES
        )

        rows = db.execute(
            text("""
                SELECT
                    worker_name,
                    worker_status,
                    current_chunk_id,
                    hostname,
                    cpu_usage,
                    memory_usage,
                    last_heartbeat,
                    created_at
                FROM worker_heartbeats
                ORDER BY last_heartbeat DESC
            """)
        ).fetchall()

        result = []
        for row in rows:
            is_stale = (
                row.last_heartbeat is None or
                row.last_heartbeat < stale_threshold
            )
            result.append({
                "worker_name":     row.worker_name,
                "worker_status":   row.worker_status if not is_stale else "OFFLINE",
                "current_chunk_id": str(row.current_chunk_id) if row.current_chunk_id else None,
                "hostname":        row.hostname,
                "cpu_usage":       float(row.cpu_usage) if row.cpu_usage else 0.0,
                "memory_usage":    float(row.memory_usage) if row.memory_usage else 0.0,
                "last_heartbeat":  row.last_heartbeat,
                "is_stale":        is_stale,
            })
        return result

    def count_active_workers(self, db: Session) -> int:
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=STALE_THRESHOLD_MINUTES
        )
        result = db.execute(
            text("""
                SELECT COUNT(*) FROM worker_heartbeats
                WHERE last_heartbeat >= :threshold
                AND worker_status != 'OFFLINE'
            """),
            {"threshold": stale_threshold}
        ).scalar()
        return int(result or 0)

    def count_total_workers(self, db: Session) -> int:
        result = db.execute(
            text("SELECT COUNT(*) FROM worker_heartbeats")
        ).scalar()
        return int(result or 0)

    # ── Platform summary ──────────────────────────────────────────────────────

    def get_total_rows_migrated_all_jobs(self, db: Session) -> int:
        result = db.query(
            func.sum(MigrationChunk.rows_processed)
        ).filter(
            MigrationChunk.status == "completed"
        ).scalar()
        return int(result or 0)
