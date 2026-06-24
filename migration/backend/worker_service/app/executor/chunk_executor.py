"""
Chunk Executor — with Prometheus Metrics
File: migration/backend/worker_service/app/executor/chunk_executor.py

REPLACES previous chunk_executor.py.

Every chunk execution now records Prometheus metrics:
    - rows_processed_total.inc(rows)
    - chunks_completed_total.inc()
    - chunk_duration_seconds.observe(seconds)
    - chunk_throughput_rows_per_second.observe(rps)
    - chunks_failed_total.inc() on failure
    - checksum_failures_total.inc() on checksum mismatch
    - worker_active_chunks.set(1) while running, .set(0) when done

Also uses:
    - IdempotentWriter (Priority 3 — no duplicates on retry)
    - ChecksumValidator (Priority 4 — data integrity proof)
"""

import time
import datetime
import json
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.shared.config.logging import logger
from backend.control_plane.app.models.migration import MigrationChunk, MigrationJob, MigrationTable
from backend.worker_service.app.readers.mysql_reader import MySQLReader
from backend.worker_service.app.readers.postgres_reader import PostgresReader
from backend.worker_service.app.writers.idempotent_writer import (
    IdempotentMySQLWriter, IdempotentPostgresWriter
)
from backend.worker_service.app.executor.retry_executor import should_retry
from backend.worker_service.app.validation.checksum_validator import ChecksumValidator
from backend.worker_service.app.monitoring.metrics_registry import (
    rows_processed_total,
    chunks_completed_total,
    chunks_failed_total,
    chunks_retried_total,
    checksum_failures_total,
    validation_failures_total,
    chunk_duration_seconds,
    chunk_throughput_rows_per_second,
    worker_active_chunks,
)

BATCH_SIZE = 5000


class ChunkExecutor:

    def __init__(self, worker_id: str):
        self.worker_id          = worker_id
        self.checksum_validator = ChecksumValidator()

    def execute(self, db: Session, job_id: str, table_id: str, chunk_id: str):
        logger.info("Executing chunk", worker_id=self.worker_id,
                    chunk_id=chunk_id, job_id=job_id)

        # ── Fetch chunk ───────────────────────────────────────────────────
        chunk = db.query(MigrationChunk).filter(MigrationChunk.id == chunk_id).first()
        if not chunk:
            logger.error("Chunk not found", chunk_id=chunk_id)
            return

        # ── Eligibility ───────────────────────────────────────────────────
        if chunk.status == "completed":
            logger.info("Chunk already completed, skipping", chunk_id=chunk_id)
            return

        if chunk.retry_count >= chunk.max_retries:
            logger.warning("Chunk at max retries", chunk_id=chunk_id)
            return

        # ── Ownership check ───────────────────────────────────────────────
        if chunk.worker_id and chunk.worker_id != self.worker_id:
            stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
            if chunk.last_heartbeat and chunk.last_heartbeat > stale_threshold:
                logger.warning("Chunk owned by active worker, skipping",
                               chunk_id=chunk_id, owner=chunk.worker_id)
                return

        # ── Fetch parents ─────────────────────────────────────────────────
        job = db.query(MigrationJob).filter(MigrationJob.id == job_id).first()
        if not job:
            return

        table = db.query(MigrationTable).filter(MigrationTable.id == table_id).first()
        if not table:
            return

        # ── Mark RUNNING ──────────────────────────────────────────────────
        chunk.status         = "running"
        chunk.worker_id      = self.worker_id
        chunk.started_at     = datetime.datetime.utcnow()
        chunk.last_heartbeat = datetime.datetime.utcnow()
        db.commit()

        # Track in Prometheus
        worker_active_chunks.labels(worker_id=self.worker_id).set(1)

        table_name = table.table_name
        pk_column  = table.primary_key_column or "id"
        pk_start   = chunk.pk_start
        pk_end     = chunk.pk_end

        start_time = time.time()

        try:
            source_reader = self._get_reader(job.source_config)
            target_writer = self._get_writer(job.target_config)

            # ── Stream source → write target ──────────────────────────────
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

                    # Heartbeat mid-chunk
                    chunk.last_heartbeat = datetime.datetime.utcnow()
                    db.commit()

            if batch:
                target_writer.insert_batch(table_name=table_name, rows=batch)
                total_rows_written += len(batch)

            # ── Row count validation ──────────────────────────────────────
            source_count = source_reader.count_rows(
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            )

            if source_count != total_rows_written:
                validation_failures_total.labels(worker_id=self.worker_id).inc()
                raise Exception(
                    f"Row count mismatch: source={source_count}, written={total_rows_written}"
                )

            # ── Checksum validation ───────────────────────────────────────
            checksum_result = self.checksum_validator.validate_chunk(
                source_config=job.source_config,
                target_config=job.target_config,
                table_name=table_name,
                pk_column=pk_column,
                pk_start=pk_start,
                pk_end=pk_end
            )

            if not checksum_result.passed:
                checksum_failures_total.labels(
                    worker_id=self.worker_id,
                    table_name=table_name
                ).inc()
                validation_failures_total.labels(worker_id=self.worker_id).inc()
                raise Exception(f"Checksum mismatch: {checksum_result.details}")

            # ── Compute metrics ───────────────────────────────────────────
            elapsed_ms  = int((time.time() - start_time) * 1000)
            elapsed_sec = elapsed_ms / 1000.0
            throughput  = round(total_rows_written / elapsed_sec, 2) if elapsed_sec > 0 else 0

            # ── Record Prometheus metrics ─────────────────────────────────
            rows_processed_total.labels(
                worker_id=self.worker_id,
                table_name=table_name
            ).inc(total_rows_written)

            chunks_completed_total.labels(worker_id=self.worker_id).inc()

            chunk_duration_seconds.labels(
                worker_id=self.worker_id,
                table_name=table_name
            ).observe(elapsed_sec)

            chunk_throughput_rows_per_second.labels(
                worker_id=self.worker_id
            ).observe(throughput)

            # ── Mark COMPLETED ────────────────────────────────────────────
            chunk.status              = "completed"
            chunk.completed_at        = datetime.datetime.utcnow()
            chunk.rows_processed      = total_rows_written
            chunk.source_row_count    = checksum_result.source_row_count
            chunk.target_row_count    = checksum_result.target_row_count
            chunk.checksum            = checksum_result.source_checksum
            chunk.validation_status   = "passed"
            chunk.duration_ms         = elapsed_ms
            chunk.throughput_rows_per_sec = throughput
            chunk.batch_size_used     = BATCH_SIZE
            db.commit()

            # ── Update parent counters ────────────────────────────────────
            self._update_table_progress(db, table)
            self._update_job_progress(db, job)

            logger.info(
                "Chunk completed",
                chunk_id=chunk_id,
                rows=total_rows_written,
                throughput_rps=f"{throughput:.0f}",
                duration_ms=elapsed_ms
            )

        except Exception as e:
            logger.error("Chunk failed", chunk_id=chunk_id, error=str(e))

            chunks_failed_total.labels(
                worker_id=self.worker_id,
                error_type=type(e).__name__
            ).inc()

            chunk.status      = "failed"
            chunk.last_error  = str(e)
            chunk.retry_count = (chunk.retry_count or 0) + 1
            chunk.worker_id   = None
            db.commit()

            if should_retry(chunk):
                self._requeue(str(job_id), str(table_id), str(chunk_id))
                chunks_retried_total.labels(worker_id=self.worker_id).inc()
                chunk.status = "retrying"
                db.commit()

        finally:
            worker_active_chunks.labels(worker_id=self.worker_id).set(0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_reader(self, config: dict):
        engine = config.get("engine", "").lower()
        if engine == "mysql":
            return MySQLReader(config=config)
        elif engine in ("postgres", "postgresql"):
            return PostgresReader(config=config)
        raise ValueError(f"Unsupported source engine: {engine}")

    def _get_writer(self, config: dict):
        engine = config.get("engine", "").lower()
        if engine == "mysql":
            return IdempotentMySQLWriter(config=config)
        elif engine in ("postgres", "postgresql"):
            return IdempotentPostgresWriter(config=config)
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
        job.failed_chunks    = failed

        if job.total_chunks and (completed + failed) >= job.total_chunks:
            job.status        = "completed" if failed == 0 else "failed"
            job.completed_at  = datetime.datetime.utcnow()

        db.commit()

    def _requeue(self, job_id: str, table_id: str, chunk_id: str):
        from backend.shared.config.redis import redis_client
        from backend.shared.constants.queues import Queues
        message = {"job_id": job_id, "table_id": table_id, "chunk_id": chunk_id, "priority": 1}
        redis_client.lpush(Queues.RETRY_QUEUE, json.dumps(message))
        logger.info("Chunk requeued for retry", chunk_id=chunk_id)
