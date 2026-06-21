"""
Job Progress Engine — Priority 5
File: migration/backend/worker_service/app/progress/job_progress_engine.py

Problem it solves:
    You have a 5TB migration running for 16 hours.
    You need to know:
        - How far along is it? (52%)
        - How many rows have been migrated? (3.4M)
        - When will it finish? (ETA: 18 minutes)
        - How fast is it going? (45,000 rows/sec)

    Without this, you're blind. With this, you can show a real-time
    progress dashboard to your paying customers.

What it computes:
    completion_pct    → 52.3%
    rows_migrated     → 3,400,000
    rows_total        → 6,500,000
    chunks_completed  → 34
    chunks_total      → 65
    chunks_failed     → 2
    throughput_rps    → 45,000 rows/sec (rolling average)
    elapsed_seconds   → 75.6 seconds
    eta_seconds       → 47 seconds remaining
    status            → "running"

Data sources:
    - migration_jobs    (totals, status, started_at)
    - migration_chunks  (per-chunk rows_processed, completed_at, duration_ms)

This is a READ-ONLY component. It never writes to the DB.
It just queries and computes.

Usage:
    from backend.worker_service.app.progress.job_progress_engine import JobProgressEngine

    engine = JobProgressEngine()
    db = next(get_db())
    progress = engine.get_progress(db, job_id="abc-123")
    print(progress)
    # {
    #     "job_id": "abc-123",
    #     "status": "running",
    #     "completion_pct": 52.3,
    #     "rows_migrated": 3400000,
    #     "rows_total": 6500000,
    #     "chunks_completed": 34,
    #     "chunks_total": 65,
    #     "chunks_failed": 2,
    #     "throughput_rps": 45000,
    #     "elapsed_seconds": 75.6,
    #     "eta_seconds": 47,
    #     "eta_human": "47 seconds",
    # }
"""

import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.control_plane.app.models.migration import MigrationJob, MigrationChunk, MigrationTable
from backend.shared.config.logging import logger


class JobProgressEngine:
    """
    Computes real-time migration progress for a given job.
    Read-only — never writes to the database.
    """

    def get_progress(self, db: Session, job_id: str) -> Dict[str, Any]:
        """
        Main method. Returns a complete progress snapshot for a job.
        """
        job = db.query(MigrationJob).filter(MigrationJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        # ── Chunk counts ──────────────────────────────────────────────────
        chunk_stats = self._get_chunk_stats(db, job_id)

        chunks_total = chunk_stats.get("total", 0)
        chunks_completed = chunk_stats.get("completed", 0)
        chunks_failed = chunk_stats.get("failed", 0)
        chunks_running = chunk_stats.get("running", 0)
        chunks_pending = chunk_stats.get("pending", 0)

        # ── Row counts ────────────────────────────────────────────────────
        rows_total = self._get_total_rows(db, job_id)
        rows_migrated = self._get_rows_migrated(db, job_id)

        # ── Completion percentage ─────────────────────────────────────────
        if chunks_total > 0:
            completion_pct = round((chunks_completed / chunks_total) * 100, 1)
        else:
            completion_pct = 0.0

        # ── Throughput (rows per second) ──────────────────────────────────
        throughput_rps = self._compute_throughput(db, job_id)

        # ── Elapsed time ──────────────────────────────────────────────────
        elapsed_seconds = self._compute_elapsed(job)

        # ── ETA ───────────────────────────────────────────────────────────
        eta_seconds = self._compute_eta(
            rows_total=rows_total,
            rows_migrated=rows_migrated,
            throughput_rps=throughput_rps
        )

        result = {
            "job_id": str(job_id),
            "status": job.status,
            "completion_pct": completion_pct,

            # Row progress
            "rows_migrated": rows_migrated,
            "rows_total": rows_total,
            "rows_remaining": max(0, rows_total - rows_migrated),

            # Chunk progress
            "chunks_completed": chunks_completed,
            "chunks_total": chunks_total,
            "chunks_failed": chunks_failed,
            "chunks_running": chunks_running,
            "chunks_pending": chunks_pending,

            # Performance
            "throughput_rps": throughput_rps,
            "throughput_human": self._format_throughput(throughput_rps),

            # Time
            "elapsed_seconds": elapsed_seconds,
            "elapsed_human": self._format_duration(elapsed_seconds),
            "eta_seconds": eta_seconds,
            "eta_human": self._format_duration(eta_seconds) if eta_seconds else "Unknown",

            # Timestamps
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

        logger.debug("Progress computed", job_id=job_id, pct=completion_pct)
        return result

    def get_table_progress(self, db: Session, job_id: str) -> list:
        """
        Returns per-table progress breakdown for a job.

        Example:
        [
            {"table": "users",   "status": "completed", "pct": 100.0, "rows": 500000},
            {"table": "orders",  "status": "running",   "pct": 45.2,  "rows": 226000},
            {"table": "products","status": "pending",   "pct": 0.0,   "rows": 0},
        ]
        """
        tables = db.query(MigrationTable).filter(MigrationTable.job_id == job_id).all()

        result = []
        for table in tables:
            if table.total_chunks and table.total_chunks > 0:
                pct = round((table.completed_chunks / table.total_chunks) * 100, 1)
            else:
                pct = 0.0

            result.append({
                "table_id": str(table.id),
                "table_name": table.table_name,
                "status": table.status,
                "completion_pct": pct,
                "total_rows": table.total_rows or 0,
                "total_chunks": table.total_chunks or 0,
                "completed_chunks": table.completed_chunks or 0,
                "failed_chunks": table.failed_chunks or 0,
            })

        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_chunk_stats(self, db: Session, job_id: str) -> Dict[str, int]:
        """
        Count chunks grouped by status.
        Returns dict like: {"completed": 34, "pending": 20, "running": 3, ...}
        """
        rows = db.query(
            MigrationChunk.status,
            func.count(MigrationChunk.id).label("count")
        ).filter(
            MigrationChunk.job_id == job_id
        ).group_by(MigrationChunk.status).all()

        stats = {row.status: row.count for row in rows}
        stats["total"] = sum(stats.values())
        return stats

    def _get_total_rows(self, db: Session, job_id: str) -> int:
        """
        Sum of total_rows across all tables in this job.
        This is set by the Planner when chunks are created.
        """
        result = db.query(
            func.sum(MigrationTable.total_rows)
        ).filter(
            MigrationTable.job_id == job_id
        ).scalar()
        return int(result or 0)

    def _get_rows_migrated(self, db: Session, job_id: str) -> int:
        """
        Sum of rows_processed across all COMPLETED chunks.
        This is the actual number of rows successfully written to target.
        """
        result = db.query(
            func.sum(MigrationChunk.rows_processed)
        ).filter(
            MigrationChunk.job_id == job_id,
            MigrationChunk.status == "completed"
        ).scalar()
        return int(result or 0)

    def _compute_throughput(self, db: Session, job_id: str) -> int:
        """
        Compute rolling average throughput in rows/second.

        Uses the last 10 completed chunks to get a recent average.
        This is more accurate than a global average because throughput
        can change as the migration progresses.

        Formula:
            rows_processed / (duration_ms / 1000)
        averaged across recent chunks.
        """
        # Get the 10 most recently completed chunks with timing data
        recent_chunks = db.query(MigrationChunk).filter(
            MigrationChunk.job_id == job_id,
            MigrationChunk.status == "completed",
            MigrationChunk.rows_processed > 0,
            MigrationChunk.duration_ms > 0
        ).order_by(
            MigrationChunk.completed_at.desc()
        ).limit(10).all()

        if not recent_chunks:
            return 0

        rates = []
        for chunk in recent_chunks:
            if chunk.duration_ms and chunk.duration_ms > 0:
                duration_secs = chunk.duration_ms / 1000.0
                rate = chunk.rows_processed / duration_secs
                rates.append(rate)

        if not rates:
            return 0

        avg_rate = sum(rates) / len(rates)
        return int(avg_rate)

    def _compute_elapsed(self, job: MigrationJob) -> Optional[float]:
        """Seconds since the job started."""
        if not job.started_at:
            return None
        now = datetime.datetime.utcnow()
        return (now - job.started_at).total_seconds()

    def _compute_eta(
        self,
        rows_total: int,
        rows_migrated: int,
        throughput_rps: int
    ) -> Optional[int]:
        """
        Estimate seconds remaining.

        Formula:
            rows_remaining / throughput_rps

        Returns None if we can't compute (no throughput data yet).
        """
        if throughput_rps <= 0 or rows_total <= 0:
            return None

        rows_remaining = max(0, rows_total - rows_migrated)
        if rows_remaining == 0:
            return 0

        eta_seconds = rows_remaining / throughput_rps
        return int(eta_seconds)

    def _format_duration(self, seconds: Optional[float]) -> str:
        """
        Convert seconds to a human-readable string.

        Examples:
            45      → "45 seconds"
            125     → "2 minutes, 5 seconds"
            3672    → "1 hour, 1 minute"
            90000   → "1 day, 1 hour"
        """
        if seconds is None:
            return "Unknown"

        seconds = int(seconds)

        if seconds < 60:
            return f"{seconds} second{'s' if seconds != 1 else ''}"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}, {secs}s"
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours} hour{'s' if hours != 1 else ''}, {minutes}m"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            return f"{days} day{'s' if days != 1 else ''}, {hours}h"

    def _format_throughput(self, rps: int) -> str:
        """
        Format rows/sec nicely.

        Examples:
            450     → "450 rows/sec"
            45000   → "45,000 rows/sec"
            1200000 → "1.2M rows/sec"
        """
        if rps <= 0:
            return "calculating..."
        elif rps < 1000:
            return f"{rps} rows/sec"
        elif rps < 1_000_000:
            return f"{rps:,} rows/sec"
        else:
            return f"{rps / 1_000_000:.1f}M rows/sec"
