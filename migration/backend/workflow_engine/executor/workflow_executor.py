"""
Workflow Executor
File: migration/backend/workflow_engine/executor/workflow_executor.py

Replaces ChunkExecutor entirely. Loads a WorkflowDefinition, walks the
DAG node-by-node, handles retries/timeouts per node, persists execution
state to workflow_executions and workflow_node_log, updates migration_chunks.

Flow:
    1. Load WorkflowDefinition (default or job-specific)
    2. Build WorkflowContext from chunk metadata + column mappings
    3. Start from entry node(s), walk DAG following edge conditions
    4. For each node:
         a. Log start to workflow_node_log
         b. Instantiate node class with config
         c. Execute with retry loop + timeout
         d. Log result
         e. Follow edges based on success/failure
    5. Persist final context to migration_chunks (rows, status, checksum, etc.)
    6. Publish chunk.completed or chunk.failed event via NotifyNode

The Worker calls execute() instead of the old ChunkExecutor.execute().
Everything else is identical from the Worker's perspective.
"""

import time
import uuid
import datetime
import json
import threading
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from backend.workflow_engine.nodes.base_node import WorkflowContext, RetryPolicy
from backend.workflow_engine.nodes.default_nodes import get_node_class
from backend.workflow_engine.definition.workflow_definition import (
    WorkflowDefinition, WorkflowDefinitionRepository, NodeDefinition
)
from backend.shared.config.logging import logger

repo = WorkflowDefinitionRepository()


class WorkflowExecutor:

    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def execute(
        self,
        db:         Session,
        job_id:     str,
        table_id:   str,
        chunk_id:   str,
        tenant_id:  str = "local",
    ) -> WorkflowContext:
        """
        Main entry point. Called by the Worker exactly where
        ChunkExecutor.execute() used to be called.

        Returns the final WorkflowContext (used for testing/debugging;
        side effects — DB writes, metrics, events — are the real output).
        """
        # ── Load chunk + job metadata ─────────────────────────────────────
        chunk = self._load_chunk(db, chunk_id)
        if not chunk:
            logger.error("Chunk not found", chunk_id=chunk_id)
            return None

        if chunk["status"] == "completed":
            logger.info("Chunk already completed, skipping", chunk_id=chunk_id)
            return None

        job   = self._load_job(db, job_id)
        table = self._load_table(db, table_id)

        if not job or not table:
            logger.error("Job or table not found", job_id=job_id, table_id=table_id)
            return None

        # ── Ownership check ───────────────────────────────────────────────
        if not self._claim_chunk(db, chunk_id, chunk):
            return None

        # ── Load workflow definition ──────────────────────────────────────
        definition = self._load_definition(db, job, tenant_id)

        # ── Load column mappings ──────────────────────────────────────────
        column_mappings = self._load_column_mappings(db, table["table_name"])

        # ── Build initial context ─────────────────────────────────────────
        source_config = job.get("source_config") or {}
        target_config = job.get("target_config") or {}
        if isinstance(source_config, str):
            source_config = json.loads(source_config)
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        ctx = WorkflowContext(
            job_id=job_id,
            chunk_id=chunk_id,
            table_name=table["table_name"],
            target_table_name=self._resolve_target_table(db, table["table_name"]),
            pk_column=table.get("primary_key_column") or "id",
            pk_start=chunk["pk_start"],
            pk_end=chunk["pk_end"],
            worker_id=self.worker_id,
            source_config=source_config,
            target_config=target_config,
            column_mappings=column_mappings,
            execution_id=str(uuid.uuid4()),
            started_at=datetime.datetime.utcnow(),
        )

        # ── Create execution record ───────────────────────────────────────
        self._create_execution_record(db, ctx, definition)

        # ── Mark chunk RUNNING ────────────────────────────────────────────
        self._update_chunk_status(db, chunk_id, "running",
                                  worker_id=self.worker_id,
                                  started_at=ctx.started_at)

        logger.info(
            "Workflow execution starting",
            chunk_id=chunk_id, job_id=job_id,
            workflow=definition.name,
            table=ctx.table_name,
            worker=self.worker_id,
        )

        # ── Execute the DAG ───────────────────────────────────────────────
        ctx = self._execute_dag(db, ctx, definition)

        # ── Persist final state to chunk ──────────────────────────────────
        self._finalize_chunk(db, chunk_id, ctx)

        # ── Update execution record ───────────────────────────────────────
        self._update_execution_record(db, ctx)

        # ── Update job + table progress ───────────────────────────────────
        self._update_table_progress(db, table_id)
        self._update_job_progress(db, job_id)

        status = "failed" if ctx.has_error else "completed"
        logger.info(
            "Workflow execution finished",
            chunk_id=chunk_id, status=status,
            rows_written=ctx.rows_written,
            error=ctx.error_message,
        )

        return ctx

    # ── DAG traversal ─────────────────────────────────────────────────────────

    def _execute_dag(
        self, db: Session, ctx: WorkflowContext, definition: WorkflowDefinition
    ) -> WorkflowContext:
        """
        Walk the workflow DAG from entry nodes, following edges based on
        each node's success/failure. Handles retries per node.
        """
        # Track which nodes have been executed
        executed = set()
        # Queue starts with entry nodes (nodes with no incoming edges)
        queue = definition.entry_nodes()

        while queue:
            node_id = queue.pop(0)
            if node_id in executed:
                continue

            node_def = definition.node_by_id(node_id)
            if not node_def:
                logger.warning("Node not found in definition", node_id=node_id)
                continue

            ctx.current_node = node_id
            ctx = self._execute_node_with_retry(db, ctx, node_def)
            executed.add(node_id)

            # Determine next nodes based on edge condition
            success = not ctx.has_error or node_id in ("metrics", "notify", "audit")
            next_nodes = definition.successors(node_id, on_success=not ctx.has_error)
            # Nodes with condition="always" always run (notify, audit, metrics)
            always_nodes = [
                e.to_node for e in definition.edges
                if e.from_node == node_id and e.condition == "always"
            ]
            for n in always_nodes:
                if n not in next_nodes:
                    next_nodes.append(n)

            for next_id in next_nodes:
                if next_id not in executed and next_id not in queue:
                    queue.append(next_id)

        return ctx

    def _execute_node_with_retry(
        self, db: Session, ctx: WorkflowContext, node_def: NodeDefinition
    ) -> WorkflowContext:
        """Execute one node with retry policy and timeout."""
        node_class = get_node_class(node_def.node_type)
        node       = node_class(node_def.config)

        rp_dict    = node_def.retry_policy or {}
        retry_policy = RetryPolicy(
            max_retries=rp_dict.get("max_retries", node.default_retry.max_retries),
            backoff_seconds=rp_dict.get("backoff_seconds", node.default_retry.backoff_seconds),
            backoff_multiplier=rp_dict.get("backoff_multiplier", node.default_retry.backoff_multiplier),
        )
        timeout    = node_def.timeout_seconds or node.default_timeout
        attempt    = 0
        last_error = None

        while attempt <= retry_policy.max_retries:
            # Log node start
            log_id = self._log_node_start(db, ctx, node_def, attempt)

            # Execute with timeout
            result_ctx, error = self._run_with_timeout(node, ctx, timeout)

            if error:
                # Timeout
                last_error = f"Node '{node_def.id}' timed out after {timeout}s"
                self._log_node_end(db, log_id, ctx, "failed", attempt, last_error)
                attempt += 1
                if attempt <= retry_policy.max_retries:
                    wait = retry_policy.wait_for_attempt(attempt)
                    logger.warning(f"Node timed out, retrying in {wait}s",
                                   node=node_def.id, attempt=attempt)
                    time.sleep(wait)
                continue

            ctx = result_ctx

            if ctx.has_error and ctx.error_node == node_def.id:
                # Node reported failure
                last_error = ctx.error_message
                self._log_node_end(db, log_id, ctx, "failed", attempt, last_error)
                attempt += 1
                if attempt <= retry_policy.max_retries:
                    wait = retry_policy.wait_for_attempt(attempt)
                    logger.warning(f"Node failed, retrying in {wait}s",
                                   node=node_def.id, attempt=attempt, error=last_error)
                    # Reset error for retry
                    ctx.has_error    = False
                    ctx.error_message = None
                    ctx.error_node   = None
                    time.sleep(wait)
                    continue
                else:
                    logger.error("Node exceeded max retries",
                                 node=node_def.id, attempts=attempt)
                    break
            else:
                # Success
                self._log_node_end(db, log_id, ctx, "completed", attempt)
                break

        return ctx

    def _run_with_timeout(self, node, ctx: WorkflowContext, timeout: int):
        """
        Run node.execute() in a thread with a timeout.
        Returns (result_ctx, error_string_or_None).
        """
        result     = [None]
        exception  = [None]

        def _run():
            try:
                result[0] = node.execute(ctx)
            except Exception as e:
                exception[0] = str(e)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # Timed out — thread still running (we can't kill it in Python,
            # but setting the error in context prevents downstream nodes from using stale data)
            return ctx, f"timeout after {timeout}s"

        if exception[0]:
            ctx.mark_error(node.node_type, exception[0])
            return ctx, None

        return result[0] if result[0] is not None else ctx, None

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _load_chunk(self, db, chunk_id):
        row = db.execute(
            text("SELECT * FROM migration_chunks WHERE id=:id"),
            {"id": chunk_id}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _load_job(self, db, job_id):
        row = db.execute(
            text("SELECT * FROM migration_jobs WHERE id=:id"),
            {"id": job_id}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _load_table(self, db, table_id):
        row = db.execute(
            text("SELECT * FROM migration_tables WHERE id=:id"),
            {"id": table_id}
        ).fetchone()
        return dict(row._mapping) if row else None

    def _claim_chunk(self, db, chunk_id, chunk) -> bool:
        stale_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
        existing_worker = chunk.get("worker_id")
        last_heartbeat  = chunk.get("last_heartbeat")

        if existing_worker and existing_worker != self.worker_id:
            if last_heartbeat and last_heartbeat > stale_threshold:
                logger.warning("Chunk owned by active worker, skipping",
                               chunk_id=chunk_id, owner=existing_worker)
                return False
        return True

    def _load_definition(self, db, job, tenant_id) -> WorkflowDefinition:
        workflow_def_id = job.get("workflow_definition_id")
        row = None

        if workflow_def_id:
            row = repo.get_by_id(db, str(workflow_def_id))
        if not row:
            row = repo.get_default(db, tenant_id)

        if row:
            return repo.from_db_row(row)

        # Emergency fallback: build default inline if DB seed didn't run
        from backend.workflow_engine.definition.workflow_definition import WorkflowDefinitionBuilder
        return (WorkflowDefinitionBuilder("standard_migration")
            .add_node("read",      "ReadNode",      "Read Source Data")
            .add_node("transform", "TransformNode", "Apply Mappings")
            .add_node("validate",  "ValidateNode",  "Pre-Write Validation")
            .add_node("write",     "WriteNode",     "Write to Target")
            .add_node("verify",    "VerifyNode",    "Verify")
            .add_node("metrics",   "MetricsNode",   "Record Metrics")
            .add_node("notify",    "NotifyNode",    "Publish Events")
            .add_node("audit",     "AuditNode",     "Write Audit Trail")
            .chain(["read", "transform", "validate", "write", "verify"])
            .add_edge("verify",  "metrics", "always")
            .add_edge("metrics", "notify",  "always")
            .add_edge("notify",  "audit",   "always")
            .build())

    def _load_column_mappings(self, db, table_name) -> list:
        try:
            from backend.schema_mapping_service.app.transformation_engine.transformer import (
                ColumnMappingConfig
            )
            rows = db.execute(
                text("""
                    SELECT scm.source_column, scm.target_column,
                           scm.source_type, scm.target_type,
                           scm.mapping_kind, scm.mapping_config,
                           scm.requires_cast, scm.cast_expression,
                           scm.conversion_safety
                    FROM schema_column_mappings scm
                    JOIN schema_table_mappings stm ON scm.table_mapping_id = stm.id
                    WHERE scm.source_table = :tname
                    ORDER BY scm.created_at
                """),
                {"tname": table_name}
            ).fetchall()

            configs = []
            for row in rows:
                mc = row.mapping_config
                if isinstance(mc, str):
                    mc = json.loads(mc) if mc else {}
                configs.append(ColumnMappingConfig(
                    source_column=row.source_column or "",
                    target_column=row.target_column or "",
                    source_type=row.source_type or "",
                    target_type=row.target_type or "",
                    mapping_kind=row.mapping_kind or "direct",
                    mapping_config=mc or {},
                    requires_cast=bool(row.requires_cast),
                    cast_expression=row.cast_expression,
                    conversion_safety=row.conversion_safety or "safe",
                ))
            return configs
        except Exception as e:
            logger.warning("Column mappings load failed, using identity",
                           table=table_name, error=str(e))
            return []

    def _resolve_target_table(self, db, source_table: str) -> str:
        try:
            row = db.execute(
                text("""
                    SELECT target_tables FROM schema_table_mappings
                    WHERE source_tables ? :tname LIMIT 1
                """),
                {"tname": source_table}
            ).fetchone()
            if row and row[0]:
                tgts = row[0] if isinstance(row[0], list) else json.loads(row[0])
                return tgts[0] if tgts else source_table
        except Exception:
            pass
        return source_table

    def _update_chunk_status(self, db, chunk_id, status, **kwargs):
        set_parts = ["status=:status", "last_heartbeat=:now"]
        params    = {"status": status, "now": datetime.datetime.utcnow(), "id": chunk_id}
        for k, v in kwargs.items():
            set_parts.append(f"{k}=:{k}")
            params[k] = v
        db.execute(
            text(f"UPDATE migration_chunks SET {', '.join(set_parts)} WHERE id=:id"),
            params
        )
        db.commit()

    def _finalize_chunk(self, db, chunk_id, ctx):
        elapsed = (datetime.datetime.utcnow() - ctx.started_at).total_seconds() if ctx.started_at else 0
        status  = "failed" if ctx.has_error else "completed"
        db.execute(
            text("""
                UPDATE migration_chunks SET
                    status               = :status,
                    rows_processed       = :rows_written,
                    source_row_count     = :src_count,
                    target_row_count     = :tgt_count,
                    checksum             = :checksum,
                    validation_status    = :val,
                    duration_ms          = :dur,
                    last_error           = :err,
                    completed_at         = :now
                WHERE id = :id
            """),
            {
                "status":    status,
                "rows_written": ctx.rows_written,
                "src_count": ctx.source_row_count,
                "tgt_count": ctx.target_row_count,
                "checksum":  ctx.source_checksum,
                "val":       "passed" if ctx.post_write_verified else "failed",
                "dur":       int(elapsed * 1000),
                "err":       ctx.error_message,
                "now":       datetime.datetime.utcnow(),
                "id":        chunk_id,
            }
        )
        db.commit()

    def _update_table_progress(self, db, table_id):
        row = db.execute(
            text("SELECT total_chunks, completed_chunks FROM migration_tables WHERE id=:id"),
            {"id": table_id}
        ).fetchone()
        if row:
            completed = (row[1] or 0) + 1
            status = "completed" if row[0] and completed >= row[0] else "running"
            db.execute(
                text("UPDATE migration_tables SET completed_chunks=:c, status=:s WHERE id=:id"),
                {"c": completed, "s": status, "id": table_id}
            )
            db.commit()

    def _update_job_progress(self, db, job_id):
        counts = db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE status='completed') AS done,
                    COUNT(*) FILTER (WHERE status='failed')    AS failed,
                    COUNT(*) AS total
                FROM migration_chunks WHERE job_id=:jid
            """),
            {"jid": job_id}
        ).fetchone()
        if counts:
            done, failed, total = counts
            status = None
            if done + failed >= total:
                status = "completed" if failed == 0 else "failed"
            if status:
                db.execute(
                    text("UPDATE migration_jobs SET status=:s, completed_at=:now, completed_chunks=:done, failed_chunks=:failed WHERE id=:id"),
                    {"s": status, "now": datetime.datetime.utcnow(), "done": done, "failed": failed, "id": job_id}
                )
            else:
                db.execute(
                    text("UPDATE migration_jobs SET completed_chunks=:done, failed_chunks=:failed WHERE id=:id"),
                    {"done": done, "failed": failed, "id": job_id}
                )
            db.commit()

    def _create_execution_record(self, db, ctx, definition):
        try:
            db.execute(
                text("""
                    INSERT INTO workflow_executions
                        (id, job_id, chunk_id, worker_id, status, started_at, current_node, created_at)
                    VALUES (:id, :jid, :cid, :wid, 'running', :now, 'read', :now)
                """),
                {"id": ctx.execution_id, "jid": ctx.job_id, "cid": ctx.chunk_id,
                 "wid": self.worker_id, "now": ctx.started_at}
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to create execution record", error=str(e))

    def _update_execution_record(self, db, ctx):
        try:
            elapsed = (datetime.datetime.utcnow() - ctx.started_at).total_seconds() if ctx.started_at else 0
            status  = "failed" if ctx.has_error else "completed"
            db.execute(
                text("""
                    UPDATE workflow_executions SET
                        status=:s, completed_at=:now, duration_ms=:dur,
                        rows_read=:rr, rows_written=:rw, rows_skipped=:rs,
                        error_message=:err, error_node=:enode,
                        context_snapshot=:snap::jsonb
                    WHERE id=:id
                """),
                {
                    "s": status, "now": datetime.datetime.utcnow(),
                    "dur": int(elapsed * 1000),
                    "rr": ctx.rows_read, "rw": ctx.rows_written, "rs": ctx.rows_skipped,
                    "err": ctx.error_message, "enode": ctx.error_node,
                    "snap": json.dumps(ctx.summary()),
                    "id": ctx.execution_id,
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to update execution record", error=str(e))

    def _log_node_start(self, db, ctx, node_def, attempt) -> str:
        log_id = str(uuid.uuid4())
        try:
            db.execute(
                text("""
                    INSERT INTO workflow_node_log
                        (id, execution_id, node_id, node_type, status, started_at,
                         retry_count, created_at)
                    VALUES (:id, :eid, :nid, :ntype, 'running', :now, :attempt, :now)
                """),
                {"id": log_id, "eid": ctx.execution_id, "nid": node_def.id,
                 "ntype": node_def.node_type, "now": datetime.datetime.utcnow(),
                 "attempt": attempt}
            )
            db.commit()
        except Exception:
            pass
        return log_id

    def _log_node_end(self, db, log_id, ctx, status, attempt, error=None):
        try:
            db.execute(
                text("""
                    UPDATE workflow_node_log SET
                        status=:s, completed_at=:now, error_message=:err
                    WHERE id=:id
                """),
                {"s": status, "now": datetime.datetime.utcnow(), "err": error, "id": log_id}
            )
            db.commit()
        except Exception:
            pass
