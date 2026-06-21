"""
Resume Manager — Priority 1
File: migration/backend/worker_service/app/resume_manager.py

This is the most critical reliability component.

Problem it solves:
    A 5TB migration runs for 16 hours.
    At hour 12, the server crashes / Docker restarts / power fails.
    Without this: you don't know what happened, you restart from zero.
    With this:    it scans the DB, finds where it left off, resumes exactly.

How it works:
    On every worker startup, BEFORE entering the main polling loop,
    call ResumeManager.resume_incomplete_jobs().

    It scans migration_chunks for any job that has:
        - PENDING chunks  → were never started, push to queue
        - RUNNING chunks  → worker crashed mid-execution, recover them
        - RETRYING chunks → were waiting for retry, push to retry queue

    COMPLETED chunks are skipped — never re-executed.

Usage (in worker.py startup):
    from backend.worker_service.app.resume_manager import ResumeManager

    resume_manager = ResumeManager()
    db = next(get_db())
    resume_manager.resume_incomplete_jobs(db)
    db.close()
"""

import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_

from backend.control_plane.app.models.migration import MigrationChunk, MigrationJob
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.shared.config.logging import logger
import json


# A chunk is considered "stuck in RUNNING" if it hasn't had a heartbeat
# update in this many minutes. Means the worker processing it is dead.
STALE_RUNNING_THRESHOLD_MINUTES = 15


class ResumeManager:
    """
    Scans the metadata database on startup and recovers all
    in-progress migration work that was interrupted.
    """

    def resume_incomplete_jobs(self, db: Session):
        """
        Main entry point. Call this once on every worker startup.

        Scans all non-terminal chunks and republishes them to the
        appropriate queue so workers can pick them up.
        """
        logger.info("ResumeManager: Scanning for incomplete jobs to resume...")

        # Find all jobs that are in a non-terminal state
        active_jobs = db.query(MigrationJob).filter(
            MigrationJob.status.in_(["running", "pending", "planning", "retrying"])
        ).all()

        if not active_jobs:
            logger.info("ResumeManager: No incomplete jobs found. Clean state.")
            return

        logger.info(
            "ResumeManager: Found incomplete jobs",
            count=len(active_jobs)
        )

        for job in active_jobs:
            self._resume_job(db, job)

    def _resume_job(self, db: Session, job: MigrationJob):
        """
        For a single job, find all chunks that need to be requeued.
        """
        job_id = str(job.id)
        logger.info("ResumeManager: Resuming job", job_id=job_id, status=job.status)

        # ── 1. PENDING chunks ──────────────────────────────────────────────
        # These were planned and saved to DB but never pushed to Redis
        # (e.g., Redis restarted and lost its queue data)
        pending_chunks = db.query(MigrationChunk).filter(
            and_(
                MigrationChunk.job_id == job.id,
                MigrationChunk.status == "pending"
            )
        ).all()

        for chunk in pending_chunks:
            self._push_to_queue(
                job_id=job_id,
                table_id=str(chunk.table_id),
                chunk_id=str(chunk.id),
                queue=Queues.MIGRATION_QUEUE
            )

        logger.info(
            "ResumeManager: Requeued PENDING chunks",
            job_id=job_id,
            count=len(pending_chunks)
        )

        # ── 2. RUNNING chunks that are stale ──────────────────────────────
        # These were being processed by a worker that crashed.
        # We know they're stale because last_heartbeat is old.
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(
            minutes=STALE_RUNNING_THRESHOLD_MINUTES
        )

        stale_running_chunks = db.query(MigrationChunk).filter(
            and_(
                MigrationChunk.job_id == job.id,
                MigrationChunk.status == "running",
                # Either heartbeat is old, or there's no heartbeat at all
                (MigrationChunk.last_heartbeat < stale_threshold) |
                (MigrationChunk.last_heartbeat == None)
            )
        ).all()

        for chunk in stale_running_chunks:
            # Release worker ownership so a new worker can claim it
            chunk.status = "pending"
            chunk.worker_id = None
            chunk.last_error = "Recovered: worker did not complete (crash/restart)"
            db.commit()

            self._push_to_queue(
                job_id=job_id,
                table_id=str(chunk.table_id),
                chunk_id=str(chunk.id),
                queue=Queues.MIGRATION_QUEUE
            )

        logger.info(
            "ResumeManager: Recovered stale RUNNING chunks",
            job_id=job_id,
            count=len(stale_running_chunks)
        )

        # ── 3. RETRYING chunks ────────────────────────────────────────────
        # These failed and were marked for retry, but the retry queue
        # may have been lost (Redis restart). Push them back.
        retrying_chunks = db.query(MigrationChunk).filter(
            and_(
                MigrationChunk.job_id == job.id,
                MigrationChunk.status == "retrying"
            )
        ).all()

        for chunk in retrying_chunks:
            # Only retry if under the max retry limit
            if chunk.retry_count < chunk.max_retries:
                chunk.status = "pending"
                db.commit()
                self._push_to_queue(
                    job_id=job_id,
                    table_id=str(chunk.table_id),
                    chunk_id=str(chunk.id),
                    queue=Queues.RETRY_QUEUE
                )
            else:
                # Permanently failed — mark it and move on
                chunk.status = "failed"
                chunk.last_error = "Exceeded max retries during recovery"
                db.commit()

        logger.info(
            "ResumeManager: Requeued RETRYING chunks",
            job_id=job_id,
            count=len(retrying_chunks)
        )

        # ── Summary for this job ──────────────────────────────────────────
        total_requeued = len(pending_chunks) + len(stale_running_chunks) + len(retrying_chunks)
        logger.info(
            "ResumeManager: Job recovery complete",
            job_id=job_id,
            total_requeued=total_requeued
        )

    def _push_to_queue(self, job_id: str, table_id: str, chunk_id: str, queue: str):
        """Push a chunk message onto the specified Redis queue."""
        message = {
            "job_id": job_id,
            "table_id": table_id,
            "chunk_id": chunk_id,
            "priority": 1
        }
        redis_client.lpush(queue, json.dumps(message))
        logger.debug(
            "ResumeManager: Pushed chunk to queue",
            chunk_id=chunk_id,
            queue=queue
        )

    def get_job_resume_summary(self, db: Session, job_id: str) -> dict:
        """
        Returns a summary of chunk states for a job.
        Useful for logging and debugging.

        Example return:
        {
            "completed": 1240,
            "pending":   48,
            "running":   3,
            "failed":    2,
            "retrying":  1,
            "total":     1294
        }
        """
        from sqlalchemy import func

        rows = db.query(
            MigrationChunk.status,
            func.count(MigrationChunk.id).label("count")
        ).filter(
            MigrationChunk.job_id == job_id
        ).group_by(MigrationChunk.status).all()

        summary = {row.status: row.count for row in rows}
        summary["total"] = sum(summary.values())
        return summary
