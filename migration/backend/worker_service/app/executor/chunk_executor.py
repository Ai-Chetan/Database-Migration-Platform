"""
Chunk Executor — Updated with Full Reliability Layer
File: migration/backend/worker_service/app/executor/chunk_executor.py

This replaces the previous chunk_executor.py.

What changed from the original:
    1. Uses IdempotentWriter instead of plain writer
       → Safe to run same chunk multiple times (no duplicates)
    2. Runs ChecksumValidator after writing
       → Proves data is not just the right count but the right VALUES
    3. Records detailed metrics (throughput, duration_ms)
       → Feeds the JobProgressEngine with real data
    4. Stores validation result in migration_chunks.checksum field
       → Full audit trail per chunk

The flow is now:
    Redis message
        ↓
    Fetch chunk from PostgreSQL
        ↓
    Validate eligibility (not completed, not over max retries)
        ↓
    Claim ownership
        ↓
    Mark RUNNING
        ↓
    Stream source rows → write to target (IDEMPOTENT)
        ↓
    Validate: row count check
        ↓
    Validate: checksum check  ← NEW
        ↓
    Store checksum + metrics  ← NEW
        ↓
    Mark COMPLETED
        ↓
    Update job progress counters
"""

import time
import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.shared.config.logging import logger
from backend.control_plane.app.models.migration import MigrationChunk, MigrationJob, MigrationTable
from backend.worker_service.app.readers.mysql_reader import MySQLReader
from backend.worker_service.app.readers.postgres_reader import PostgresReader
from backend.worker_service.app.writers.idempotent_writer import IdempotentMySQLWriter, IdempotentPostgresWriter
from backend.worker_service.app.executor.retry_executor import should_retry
from backend.worker_service.app.validation.checksum_validator import ChecksumValidator

import json


BATCH_SIZE = 5000


class ChunkExecutor:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.checksum_validator = ChecksumValidator()

    def execute(self, db: Session, job_id: str, table_id: str, chunk_id: str):
        """
        Full chunk execution lifecycle with all reliability components active.
        """
        logger.info(
            "Executing chunk",
            worker_id=self.worker_id,
            chunk_id=chunk_id,
            job_id=job_id
        )

        # ── Step 1: Fetch chunk ───────────────────────────────────────────
        chunk = db.query(MigrationChunk).filter(MigrationChunk.id == chunk_id).first()
        if not chunk:
            logger.error("Chunk not found", chunk_id=chunk_id)
            return

        # ── Step 2: Eligibility checks ────────────────────────────────────
        if chunk.status in ("completed",):
            logger.info("Chunk already completed, skipping", chunk_id=chunk_id)
            return

        if chunk.retry_count >= chunk.max_retries:
            logger.warning(
                "Chunk at max retries",
                chunk_id=chunk_id,
                retry_count=chunk.retry_count
            )
            return

        # ── Step 3: Claim ownership ───────────────────────────────────────
        # If another live worker already claimed this, back off.
        # (Stale worker ownership is handled by StaleChunkRecovery.)
        if chunk.worker_id and chunk.worker_id != self.worker_id:
            # Check if the existing claim is stale
            stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
            if chunk.last_heartbeat and chunk.last_heartbeat > stale_threshold:
                logger.warning(
                    "Chunk claimed by active worker, skipping",
                    chunk_id=chunk_id,
                    claimed_by=chunk.worker_id
                )
                return
            # Heartbeat is stale — take over
            logger.info(
                "Taking over stale chunk from dead worker",
                chunk_id=chunk_id,
                previous_worker=chunk.worker_id
            )

        # ── Step 4: Fetch job and table ───────────────────────────────────
        job = db.query(MigrationJob).filter(MigrationJob.id == job_id).first()
        if not job:
            logger.error("Job not found", job_id=job_id)
            return

        table = db.query(MigrationTable).filter(MigrationTable.id == table_id).first()
        if not table:
            logger.error("Table not found", table_id=table_id)
            return

        # ── Step 5: Mark RUNNING ──────────────────────────────────────────
        chunk.status = "running"
        chunk.worker_id = self.worker_id
        chunk.started_at = datetime.datetime.utcnow()
        chunk.last_heartbeat = datetime.datetime.utcnow()
        db.commit()

        start_time = time.time()

        try:
            source_config = job.source_config
            target_config = job.target_config

            source_reader = self._get_reader(source_config)
            target_writer = self._get_writer(target_config)

            table_name = table.table_name
            pk_column = table.primary_key_column or "id"
            pk_start = chunk.pk_start
            pk_end = chunk.pk_end

            logger.info(
                "Data transfer starting",
                chunk_id=chunk_id,
                table=table_name,
                pk_range=f"{pk_start}→{pk_end}"
            )

            # ── Step 6: Stream + Write (IDEMPOTENT) ───────────────────────
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
                    target_writer.insert_batch(table_name=table_name, rows=batch)
                    total_rows_written += len(batch)
                    batch = []

                    # Update heartbeat mid-chunk so stale detection doesn't fire
                    chunk.last_heartbeat = datetime.datetime.utcnow()
                    db.commit()

            # Final partial batch
            if batch:
                target_writer.insert_batch(table_name=table_name, rows=batch)
                total_rows_written += len(batch)

            # ── Step 7: Row count validation ──────────────────────────────
            source_count = source_reader.count_rows(
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            )

            if source_count != total_rows_written:
                raise Exception(
                    f"Row count mismatch: source={source_count}, written={total_rows_written}"
                )

            # ── Step 8: Checksum validation ───────────────────────────────
            checksum_result = self.checksum_validator.validate_chunk(
                source_config=source_config,
                target_config=target_config,
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            )

            if not checksum_result.passed:
                raise Exception(
                    f"Checksum validation failed: {checksum_result.details}"
                )

            # ── Step 9: Compute metrics ───────────────────────────────────
            elapsed_ms = int((time.time() - start_time) * 1000)
            elapsed_secs = elapsed_ms / 1000.0
            throughput_rps = round(total_rows_written / elapsed_secs, 2) if elapsed_secs > 0 else 0

            # ── Step 10: Mark COMPLETED ───────────────────────────────────
            chunk.status = "completed"
            chunk.completed_at = datetime.datetime.utcnow()
            chunk.rows_processed = total_rows_written
            chunk.source_row_count = checksum_result.source_row_count
            chunk.target_row_count = checksum_result.target_row_count
            chunk.checksum = checksum_result.source_checksum
            chunk.validation_status = "passed"
            chunk.duration_ms = elapsed_ms
            chunk.throughput_rows_per_sec = throughput_rps
            chunk.batch_size_used = BATCH_SIZE
            db.commit()

            # ── Step 11: Update table and job counters ────────────────────
            self._update_table_progress(db, table)
            self._update_job_progress(db, job)

            logger.info(
                "Chunk completed",
                chunk_id=chunk_id,
                rows=total_rows_written,
                throughput_rps=f"{throughput_rps:.0f}",
                duration_ms=elapsed_ms,
                checksum=checksum_result.source_checksum[:8] + "..."
            )

        except Exception as e:
            logger.error(
                "Chunk execution failed",
                chunk_id=chunk_id,
                error=str(e),
                worker_id=self.worker_id
            )

            chunk.status = "failed"
            chunk.last_error = str(e)
            chunk.retry_count = (chunk.retry_count or 0) + 1
            chunk.worker_id = None
            db.commit()

            if should_retry(chunk):
                self._requeue(str(job_id), str(table_id), str(chunk_id))
                chunk.status = "retrying"
                db.commit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_reader(self, source_config: dict):
        engine = source_config.get("engine", "").lower()
        if engine == "mysql":
            return MySQLReader(config=source_config)
        elif engine in ("postgres", "postgresql"):
            return PostgresReader(config=source_config)
        else:
            raise ValueError(f"Unsupported source engine: {engine}")

    def _get_writer(self, target_config: dict):
        """
        Returns IDEMPOTENT writer — uses INSERT IGNORE / ON CONFLICT DO NOTHING.
        Safe to run multiple times with the same data.
        """
        engine = target_config.get("engine", "").lower()
        if engine == "mysql":
            return IdempotentMySQLWriter(config=target_config)
        elif engine in ("postgres", "postgresql"):
            return IdempotentPostgresWriter(config=target_config)
        else:
            raise ValueError(f"Unsupported target engine: {engine}")

    def _update_table_progress(self, db: Session, table: MigrationTable):
        table.completed_chunks = (table.completed_chunks or 0) + 1
        if table.total_chunks and table.completed_chunks >= table.total_chunks:
            table.status = "completed"
        db.commit()

    def _update_job_progress(self, db: Session, job: MigrationJob):
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

        if job.total_chunks and (completed + failed) >= job.total_chunks:
            if failed == 0:
                job.status = "completed"
                job.completed_at = datetime.datetime.utcnow()
            else:
                job.status = "failed"

        db.commit()

    def _requeue(self, job_id: str, table_id: str, chunk_id: str):
        from backend.shared.config.redis import redis_client
        from backend.shared.constants.queues import Queues
        message = {"job_id": job_id, "table_id": table_id, "chunk_id": chunk_id, "priority": 1}
        redis_client.lpush(Queues.RETRY_QUEUE, json.dumps(message))
        logger.info("Chunk requeued for retry", chunk_id=chunk_id)
