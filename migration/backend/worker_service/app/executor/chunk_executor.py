"""
Chunk Executor - Core Execution Engine
File: migration/backend/worker_service/app/executor/chunk_executor.py

This is the most important file in the Worker Service.

It orchestrates the full chunk execution lifecycle:
    1. Fetch chunk metadata from PostgreSQL
    2. Claim ownership (prevent duplicate execution)
    3. Mark chunk as RUNNING
    4. Read source rows (streaming, memory-safe)
    5. Write rows to target database
    6. Validate row counts
    7. Mark chunk as COMPLETED (or FAILED)
    8. Update parent job/table progress counters

Every chunk goes through this exact flow.
"""

import time
import datetime
from sqlalchemy.orm import Session

from backend.shared.config.logging import logger
from backend.control_plane.app.models.migration import MigrationChunk, MigrationJob, MigrationTable
from backend.worker_service.app.readers.mysql_reader import MySQLReader
from backend.worker_service.app.readers.postgres_reader import PostgresReader
from backend.worker_service.app.writers.mysql_writer import MySQLWriter
from backend.worker_service.app.writers.postgres_writer import PostgresWriter
from backend.worker_service.app.executor.transaction_manager import TransactionManager
from backend.worker_service.app.executor.retry_executor import should_retry


# How many rows to insert per batch
BATCH_SIZE = 5000


class ChunkExecutor:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def execute(self, db: Session, job_id: str, table_id: str, chunk_id: str):
        """
        Full chunk execution lifecycle.
        This method is the core of the entire migration platform.
        """
        logger.info(
            "Executing chunk",
            worker_id=self.worker_id,
            chunk_id=chunk_id,
            job_id=job_id
        )

        # ── Step 1: Fetch chunk metadata ──────────────────────────────────
        chunk = db.query(MigrationChunk).filter(MigrationChunk.id == chunk_id).first()
        if not chunk:
            logger.error("Chunk not found in database", chunk_id=chunk_id)
            return

        # ── Step 2: Validate chunk is eligible for execution ──────────────
        if chunk.status in ("completed", "COMPLETED"):
            logger.info("Chunk already completed, skipping", chunk_id=chunk_id)
            return

        if chunk.retry_count >= chunk.max_retries:
            logger.warning(
                "Chunk exceeded max retries, skipping",
                chunk_id=chunk_id,
                retry_count=chunk.retry_count,
                max_retries=chunk.max_retries
            )
            return

        # ── Step 3: Claim ownership - prevent duplicate execution ─────────
        # If another worker already claimed this chunk, skip it
        if chunk.worker_id and chunk.worker_id != self.worker_id:
            logger.warning(
                "Chunk already claimed by another worker",
                chunk_id=chunk_id,
                claimed_by=chunk.worker_id,
                my_worker_id=self.worker_id
            )
            return

        # ── Step 4: Fetch parent job and table ────────────────────────────
        job = db.query(MigrationJob).filter(MigrationJob.id == job_id).first()
        if not job:
            logger.error("Job not found", job_id=job_id)
            return

        table = db.query(MigrationTable).filter(MigrationTable.id == table_id).first()
        if not table:
            logger.error("Table not found", table_id=table_id)
            return

        # ── Step 5: Mark chunk as RUNNING ─────────────────────────────────
        chunk.status = "running"
        chunk.worker_id = self.worker_id
        chunk.started_at = datetime.datetime.utcnow()
        chunk.last_heartbeat = datetime.datetime.utcnow()
        db.commit()

        start_time = time.time()

        try:
            # ── Step 6: Build source & target DB connections ───────────────
            source_config = job.source_config  # e.g. {"engine": "mysql", "host": ..., "port": ..., ...}
            target_config = job.target_config  # e.g. {"engine": "postgres", ...}

            source_reader = self._get_reader(source_config)
            target_writer = self._get_writer(target_config)

            table_name = table.table_name
            pk_column = table.primary_key_column or "id"
            pk_start = chunk.pk_start
            pk_end = chunk.pk_end

            logger.info(
                "Starting data transfer",
                chunk_id=chunk_id,
                table=table_name,
                pk_start=pk_start,
                pk_end=pk_end
            )

            # ── Step 7: Stream source rows and write to target ─────────────
            total_rows_written = 0
            batch = []

            for row in source_reader.stream_chunk(
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            ):
                batch.append(row)

                if len(batch) >= BATCH_SIZE:
                    rows_written = target_writer.insert_batch(
                        table_name=table_name,
                        rows=batch
                    )
                    total_rows_written += rows_written
                    batch = []

                    # Update heartbeat while processing large chunks
                    chunk.last_heartbeat = datetime.datetime.utcnow()
                    db.commit()

            # Write any remaining rows in the last partial batch
            if batch:
                rows_written = target_writer.insert_batch(
                    table_name=table_name,
                    rows=batch
                )
                total_rows_written += rows_written

            # ── Step 8: Validate row counts ────────────────────────────────
            source_count = source_reader.count_rows(
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            )

            logger.info(
                "Row count validation",
                chunk_id=chunk_id,
                source_rows=source_count,
                written_rows=total_rows_written
            )

            if source_count != total_rows_written:
                raise Exception(
                    f"Row count mismatch: source={source_count}, written={total_rows_written}"
                )

            # ── Step 9: Mark chunk as COMPLETED ───────────────────────────
            elapsed_ms = int((time.time() - start_time) * 1000)

            chunk.status = "completed"
            chunk.completed_at = datetime.datetime.utcnow()
            chunk.rows_processed = total_rows_written
            chunk.source_row_count = source_count
            chunk.target_row_count = total_rows_written
            chunk.validation_status = "passed"
            chunk.duration_ms = elapsed_ms
            db.commit()

            # ── Step 10: Update parent table and job counters ──────────────
            self._increment_table_progress(db, table)
            self._update_job_progress(db, job)

            logger.info(
                "Chunk completed successfully",
                chunk_id=chunk_id,
                rows=total_rows_written,
                duration_ms=elapsed_ms,
                worker_id=self.worker_id
            )

        except Exception as e:
            # ── Failure Handling ───────────────────────────────────────────
            logger.error(
                "Chunk execution failed",
                chunk_id=chunk_id,
                error=str(e),
                worker_id=self.worker_id
            )

            chunk.status = "failed"
            chunk.last_error = str(e)
            chunk.retry_count = (chunk.retry_count or 0) + 1
            chunk.worker_id = None  # Release ownership so another worker can retry
            db.commit()

            # Requeue for retry if eligible
            if should_retry(chunk):
                self._requeue_chunk(job_id=str(job_id), table_id=str(table_id), chunk_id=str(chunk_id))
                chunk.status = "retrying"
                db.commit()

    def _get_reader(self, source_config: dict):
        """Return the appropriate reader based on source DB engine."""
        engine = source_config.get("engine", "").lower()
        if engine == "mysql":
            return MySQLReader(config=source_config)
        elif engine in ("postgres", "postgresql"):
            return PostgresReader(config=source_config)
        else:
            raise ValueError(f"Unsupported source database engine: {engine}")

    def _get_writer(self, target_config: dict):
        """Return the appropriate writer based on target DB engine."""
        engine = target_config.get("engine", "").lower()
        if engine == "mysql":
            return MySQLWriter(config=target_config)
        elif engine in ("postgres", "postgresql"):
            return PostgresWriter(config=target_config)
        else:
            raise ValueError(f"Unsupported target database engine: {engine}")

    def _increment_table_progress(self, db: Session, table: MigrationTable):
        """Increment completed chunk count on the parent table."""
        table.completed_chunks = (table.completed_chunks or 0) + 1
        if table.total_chunks and table.completed_chunks >= table.total_chunks:
            table.status = "completed"
        db.commit()

    def _update_job_progress(self, db: Session, job: MigrationJob):
        """Recalculate and update job-level progress counters."""
        from sqlalchemy import func
        from backend.control_plane.app.models.migration import MigrationChunk

        completed = db.query(func.count(MigrationChunk.id)).filter(
            MigrationChunk.job_id == job.id,
            MigrationChunk.status == "completed"
        ).scalar() or 0

        failed = db.query(func.count(MigrationChunk.id)).filter(
            MigrationChunk.job_id == job.id,
            MigrationChunk.status == "failed"
        ).scalar() or 0

        job.completed_chunks = completed
        job.failed_chunks = failed

        # If all chunks are done, mark the job completed
        if job.total_chunks and (completed + failed) >= job.total_chunks:
            if failed == 0:
                job.status = "completed"
                job.completed_at = datetime.datetime.utcnow()
            else:
                job.status = "failed"

        db.commit()

    def _requeue_chunk(self, job_id: str, table_id: str, chunk_id: str):
        """Push a failed chunk back onto the retry queue."""
        import json
        from backend.shared.config.redis import redis_client
        from backend.shared.constants.queues import Queues

        message = {
            "job_id": job_id,
            "table_id": table_id,
            "chunk_id": chunk_id,
            "priority": 1
        }
        redis_client.lpush(Queues.RETRY_QUEUE, json.dumps(message))
        logger.info("Chunk requeued for retry", chunk_id=chunk_id)
