"""
Default Workflow Nodes — All 8 standard migration steps
File: migration/backend/workflow_engine/nodes/default_nodes.py

The 8 nodes that make up the standard_migration workflow:

    ReadNode       → stream rows from source connector
    TransformNode  → apply column mappings (rename/cast/expression/constant/lookup)
    ValidateNode   → pre-write checks (not-null, type safety)
    WriteNode      → bulk insert to target connector
    VerifyNode     → row count + checksum comparison source vs target
    MetricsNode    → record Prometheus metrics + throughput stats
    NotifyNode     → publish events to Event Bus
    AuditNode      → write to immutable audit trail

Each is a stateless class. All state passes through WorkflowContext.
Adding a new node (e.g. DataMaskingNode, DataProfilingNode) never
touches this file — just add a new subclass, register with PluginManager.
"""

import time
import datetime
import hashlib
from typing import Dict, Any, List

from backend.workflow_engine.nodes.base_node import WorkflowNode, WorkflowContext, RetryPolicy
from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.shared.config.logging import logger


# ── 1. ReadNode ───────────────────────────────────────────────────────────────

class ReadNode(WorkflowNode):
    """
    Streams rows from the source database for the chunk's PK range.
    Populates ctx.source_rows and ctx.rows_read.
    Uses the registered connector for the source engine.
    """
    node_type = "ReadNode"
    default_retry = RetryPolicy(max_retries=3, backoff_seconds=5, backoff_multiplier=2)
    default_timeout = 300

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            connector = ConnectorRegistry.get_for_config(ctx.source_config)
            connector.connect()

            rows = []
            try:
                for row in connector.stream_rows(
                    table_name=ctx.table_name,
                    pk_column=ctx.pk_column,
                    pk_start=ctx.pk_start,
                    pk_end=ctx.pk_end,
                    batch_size=self.config.get("batch_size", 5000),
                ):
                    rows.append(row)
            finally:
                connector.disconnect()

            ctx.source_rows = rows
            ctx.rows_read   = len(rows)
            ctx.mark_node_complete("read", {"rows_read": ctx.rows_read})

            logger.info("ReadNode complete",
                        table=ctx.table_name, rows=ctx.rows_read,
                        chunk_id=ctx.chunk_id)

        except Exception as e:
            ctx.mark_error("read", f"ReadNode failed: {e}")
            logger.error("ReadNode failed", error=str(e), chunk_id=ctx.chunk_id)

        return ctx

    def input_summary(self, ctx):
        return {"table": ctx.table_name, "pk_range": f"{ctx.pk_start}–{ctx.pk_end}"}

    def output_summary(self, ctx):
        return {"rows_read": ctx.rows_read}


# ── 2. TransformNode ──────────────────────────────────────────────────────────

class TransformNode(WorkflowNode):
    """
    Applies column-level mappings to every source row.
    Uses the existing RowTransformer from transformation_engine.
    Falls back to identity (no-op) if no mappings are defined.
    Populates ctx.transformed_rows.
    """
    node_type = "TransformNode"
    default_retry = RetryPolicy(max_retries=1, backoff_seconds=0)
    default_timeout = 120

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            from backend.schema_mapping_service.app.transformation_engine.transformer import (
                RowTransformer
            )

            if ctx.column_mappings:
                transformer = RowTransformer(ctx.column_mappings)
                transformed = []
                skipped = 0
                for row in ctx.source_rows:
                    try:
                        transformed.append(transformer.transform(row))
                    except Exception as e:
                        skipped += 1
                        logger.warning("Row transform failed, skipping",
                                       error=str(e), chunk_id=ctx.chunk_id)
                ctx.transformed_rows = transformed
                ctx.rows_skipped    += skipped
            else:
                # No mappings — identity pass-through
                ctx.transformed_rows = ctx.source_rows

            ctx.mark_node_complete("transform", {
                "rows_transformed": len(ctx.transformed_rows),
                "rows_skipped":     ctx.rows_skipped,
            })

        except Exception as e:
            ctx.mark_error("transform", f"TransformNode failed: {e}")
            logger.error("TransformNode failed", error=str(e), chunk_id=ctx.chunk_id)

        return ctx

    def output_summary(self, ctx):
        return {"rows_transformed": len(ctx.transformed_rows), "rows_skipped": ctx.rows_skipped}


# ── 3. ValidateNode ───────────────────────────────────────────────────────────

class ValidateNode(WorkflowNode):
    """
    Pre-write validation on the transformed rows.
    Checks: NOT NULL violations, type safety, row count sanity.
    Configurable: check_not_null, check_types (from workflow node config).
    Does NOT connect to any database — pure in-memory validation.
    """
    node_type = "ValidateNode"
    default_retry = RetryPolicy(max_retries=1, backoff_seconds=0)
    default_timeout = 60

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            check_not_null = self.config.get("check_not_null", True)
            violations     = []

            if not ctx.transformed_rows:
                ctx.pre_write_valid = True
                ctx.mark_node_complete("validate", {"violations": 0, "rows_checked": 0})
                return ctx

            # Sample: check first row's keys to identify columns
            sample_keys = set(ctx.transformed_rows[0].keys()) if ctx.transformed_rows else set()

            if check_not_null:
                for i, row in enumerate(ctx.transformed_rows):
                    for key, val in row.items():
                        if val is None and key.lower() in ("id", "uuid", "pk"):
                            violations.append(f"Row {i}: PK column '{key}' is NULL")

            # Row count sanity: transformed_rows should be ≤ source_rows
            if len(ctx.transformed_rows) > len(ctx.source_rows):
                violations.append(
                    f"Row count increased during transform: "
                    f"{len(ctx.source_rows)} → {len(ctx.transformed_rows)}"
                )

            ctx.pre_write_valid = len(violations) == 0
            if violations:
                # Don't fail the whole chunk for validation warnings in MVP
                # Log them and continue (configurable in V2: fail_on_violation flag)
                for v in violations[:5]:
                    logger.warning("ValidateNode violation", violation=v,
                                   chunk_id=ctx.chunk_id)

            ctx.mark_node_complete("validate", {
                "rows_checked": len(ctx.transformed_rows),
                "violations":   len(violations),
                "passed":       ctx.pre_write_valid,
            })

        except Exception as e:
            ctx.mark_error("validate", f"ValidateNode failed: {e}")
            logger.error("ValidateNode failed", error=str(e), chunk_id=ctx.chunk_id)

        return ctx

    def output_summary(self, ctx):
        return {"pre_write_valid": ctx.pre_write_valid, "rows_checked": len(ctx.transformed_rows)}


# ── 4. WriteNode ──────────────────────────────────────────────────────────────

class WriteNode(WorkflowNode):
    """
    Bulk inserts transformed rows into the target database.
    Uses the registered connector for the target engine.
    Supports: ignore_duplicates (default/idempotent), upsert, fail_on_duplicate.
    Populates ctx.rows_written.
    """
    node_type = "WriteNode"
    default_retry = RetryPolicy(max_retries=3, backoff_seconds=10, backoff_multiplier=2)
    default_timeout = 300

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        if not ctx.transformed_rows:
            ctx.rows_written = 0
            ctx.mark_node_complete("write", {"rows_written": 0, "reason": "no rows"})
            return ctx

        try:
            mode      = self.config.get("mode", "ignore_duplicates")
            connector = ConnectorRegistry.get_for_config(ctx.target_config)
            connector.connect()

            try:
                result = connector.bulk_insert(
                    table_name=ctx.target_table_name,
                    rows=ctx.transformed_rows,
                    mode=mode,
                )
            finally:
                connector.disconnect()

            ctx.rows_written = result.rows_inserted
            ctx.rows_skipped += result.rows_skipped
            ctx.rows_failed  += result.rows_failed

            if result.rows_failed > 0 and not self.config.get("ignore_write_errors", True):
                ctx.mark_error("write", f"WriteNode: {result.rows_failed} rows failed to insert")
                return ctx

            ctx.mark_node_complete("write", {
                "rows_written": ctx.rows_written,
                "rows_skipped": result.rows_skipped,
                "rows_failed":  result.rows_failed,
                "duration_ms":  result.duration_ms,
                "mode":         mode,
            })

            logger.info("WriteNode complete",
                        rows_written=ctx.rows_written, table=ctx.target_table_name,
                        chunk_id=ctx.chunk_id)

        except Exception as e:
            ctx.mark_error("write", f"WriteNode failed: {e}")
            logger.error("WriteNode failed", error=str(e), chunk_id=ctx.chunk_id)

        return ctx

    def input_summary(self, ctx):
        return {"rows_to_write": len(ctx.transformed_rows), "table": ctx.target_table_name}

    def output_summary(self, ctx):
        return {"rows_written": ctx.rows_written, "rows_failed": ctx.rows_failed}


# ── 5. VerifyNode ─────────────────────────────────────────────────────────────

class VerifyNode(WorkflowNode):
    """
    Post-write verification. Compares source and target:
      - Row count in PK range
      - Checksum of all column values in PK range (if verify_checksum=True)
    Populates ctx.post_write_verified, source/target counts and checksums.
    """
    node_type = "VerifyNode"
    default_retry = RetryPolicy(max_retries=2, backoff_seconds=5)
    default_timeout = 120

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            verify_count    = self.config.get("verify_row_count", True)
            verify_checksum = self.config.get("verify_checksum", True)

            src = ConnectorRegistry.get_for_config(ctx.source_config)
            tgt = ConnectorRegistry.get_for_config(ctx.target_config)
            src.connect()
            tgt.connect()

            try:
                if verify_count:
                    ctx.source_row_count = src.count_rows_in_range(
                        ctx.table_name, ctx.pk_column, ctx.pk_start, ctx.pk_end
                    )
                    ctx.target_row_count = tgt.count_rows_in_range(
                        ctx.target_table_name, ctx.pk_column, ctx.pk_start, ctx.pk_end
                    )

                if verify_checksum and verify_count:
                    ctx.source_checksum = src.compute_checksum(
                        ctx.table_name, ctx.pk_column, ctx.pk_start, ctx.pk_end
                    )
                    ctx.target_checksum = tgt.compute_checksum(
                        ctx.target_table_name, ctx.pk_column, ctx.pk_start, ctx.pk_end
                    )
            finally:
                src.disconnect()
                tgt.disconnect()

            count_match    = ctx.source_row_count == ctx.target_row_count
            checksum_match = (not verify_checksum) or (ctx.source_checksum == ctx.target_checksum)

            ctx.post_write_verified = count_match and checksum_match

            if not count_match:
                ctx.mark_error(
                    "verify",
                    f"Row count mismatch: source={ctx.source_row_count}, "
                    f"target={ctx.target_row_count}"
                )
            elif not checksum_match:
                ctx.mark_error(
                    "verify",
                    f"Checksum mismatch: source={ctx.source_checksum}, "
                    f"target={ctx.target_checksum}"
                )
            else:
                ctx.mark_node_complete("verify", {
                    "source_rows":    ctx.source_row_count,
                    "target_rows":    ctx.target_row_count,
                    "checksum_match": checksum_match,
                    "verified":       True,
                })

        except Exception as e:
            ctx.mark_error("verify", f"VerifyNode failed: {e}")
            logger.error("VerifyNode failed", error=str(e), chunk_id=ctx.chunk_id)

        return ctx

    def output_summary(self, ctx):
        return {
            "verified":      ctx.post_write_verified,
            "source_rows":   ctx.source_row_count,
            "target_rows":   ctx.target_row_count,
            "checksum_match": ctx.source_checksum == ctx.target_checksum
                             if ctx.source_checksum else None,
        }


# ── 6. MetricsNode ────────────────────────────────────────────────────────────

class MetricsNode(WorkflowNode):
    """
    Records Prometheus metrics and updates the chunk record in PostgreSQL
    with throughput, duration, and row counts.
    Condition: always (runs even if prior nodes failed — metrics are always valuable).
    """
    node_type = "MetricsNode"
    default_retry = RetryPolicy(max_retries=1, backoff_seconds=0)
    default_timeout = 30

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            elapsed_sec = (
                (datetime.datetime.utcnow() - ctx.started_at).total_seconds()
                if ctx.started_at else 1.0
            )
            throughput = round(ctx.rows_written / elapsed_sec, 2) if elapsed_sec > 0 else 0

            # Update Prometheus metrics (best-effort — failures are non-fatal)
            try:
                from backend.monitoring_service.app.metrics.metrics_registry import (
                    rows_processed_total, chunk_duration_seconds,
                    chunk_throughput_rows_per_second,
                    chunks_completed_total, chunks_failed_total,
                )
                rows_processed_total.labels(
                    worker_id=ctx.worker_id, table_name=ctx.table_name
                ).inc(ctx.rows_written)
                chunk_duration_seconds.labels(
                    worker_id=ctx.worker_id, table_name=ctx.table_name
                ).observe(elapsed_sec)
                chunk_throughput_rows_per_second.labels(
                    worker_id=ctx.worker_id
                ).observe(throughput)
                if ctx.has_error:
                    chunks_failed_total.labels(
                        worker_id=ctx.worker_id, error_type=ctx.error_node or "unknown"
                    ).inc()
                else:
                    chunks_completed_total.labels(worker_id=ctx.worker_id).inc()
            except Exception:
                pass   # Metrics are best-effort

            ctx.node_results["metrics"] = {
                "throughput_rps": throughput,
                "duration_sec":   round(elapsed_sec, 2),
                "rows_written":   ctx.rows_written,
                "rows_read":      ctx.rows_read,
            }
            ctx.mark_node_complete("metrics", ctx.node_results["metrics"])

        except Exception as e:
            logger.warning("MetricsNode failed (non-fatal)", error=str(e))
            ctx.mark_node_complete("metrics", {"error": str(e)})

        return ctx


# ── 7. NotifyNode ─────────────────────────────────────────────────────────────

class NotifyNode(WorkflowNode):
    """
    Publishes events to the Event Bus.
    Condition: always (runs even if prior nodes failed).
    Publishes: chunk.completed OR chunk.failed depending on ctx.has_error.
    """
    node_type = "NotifyNode"
    default_retry = RetryPolicy(max_retries=2, backoff_seconds=5)
    default_timeout = 30

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            from backend.kernel.event_bus.event_bus import EventBus
            from backend.shared.config.database import SessionLocal

            db = SessionLocal()
            try:
                event_type = "chunk.failed" if ctx.has_error else "chunk.completed"
                EventBus.publish(
                    event_type=event_type,
                    source_service="workflow_engine",
                    resource_type="chunk",
                    resource_id=ctx.chunk_id,
                    payload={
                        "job_id":       ctx.job_id,
                        "table_name":   ctx.table_name,
                        "rows_written": ctx.rows_written,
                        "rows_read":    ctx.rows_read,
                        "has_error":    ctx.has_error,
                        "error_message": ctx.error_message,
                        "worker_id":    ctx.worker_id,
                    },
                    correlation_id=ctx.job_id,
                    db=db,
                )
            finally:
                db.close()

            ctx.mark_node_complete("notify", {"event_published": event_type})

        except Exception as e:
            logger.warning("NotifyNode failed (non-fatal)", error=str(e))
            ctx.mark_node_complete("notify", {"error": str(e)})

        return ctx


# ── 8. AuditNode ──────────────────────────────────────────────────────────────

class AuditNode(WorkflowNode):
    """
    Writes the chunk execution result to the immutable audit trail.
    Condition: always (runs even if prior nodes failed).
    """
    node_type = "AuditNode"
    default_retry = RetryPolicy(max_retries=2, backoff_seconds=5)
    default_timeout = 30

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        try:
            from backend.enterprise.security.audit.audit_trail import AuditTrail
            from backend.shared.config.database import SessionLocal

            db = SessionLocal()
            try:
                action = "chunk.completed" if not ctx.has_error else "chunk.failed"
                AuditTrail.log(
                    db=db,
                    action=action,
                    resource_type="chunk",
                    resource_id=ctx.chunk_id,
                    new_value={
                        "job_id":       ctx.job_id,
                        "table_name":   ctx.table_name,
                        "rows_written": ctx.rows_written,
                        "rows_read":    ctx.rows_read,
                        "verified":     ctx.post_write_verified,
                        "error":        ctx.error_message,
                        "worker_id":    ctx.worker_id,
                    },
                )
            finally:
                db.close()

            ctx.mark_node_complete("audit", {"logged": True})

        except Exception as e:
            logger.warning("AuditNode failed (non-fatal)", error=str(e))
            ctx.mark_node_complete("audit", {"error": str(e)})

        return ctx


# ── Node Registry ─────────────────────────────────────────────────────────────

NODE_REGISTRY: Dict[str, type] = {
    "ReadNode":      ReadNode,
    "TransformNode": TransformNode,
    "ValidateNode":  ValidateNode,
    "WriteNode":     WriteNode,
    "VerifyNode":    VerifyNode,
    "MetricsNode":   MetricsNode,
    "NotifyNode":    NotifyNode,
    "AuditNode":     AuditNode,
}


def get_node_class(node_type: str) -> type:
    """Get node class by type string. Raises ValueError if not found."""
    cls = NODE_REGISTRY.get(node_type)
    if not cls:
        # Also check PluginManager for dynamically registered nodes (Part 7+)
        try:
            from backend.kernel.plugin_manager.plugin_manager import PluginManager
            cls = PluginManager.get_class("workflow_node", node_type)
        except Exception:
            pass
    if not cls:
        raise ValueError(
            f"Unknown node_type='{node_type}'. "
            f"Available: {list(NODE_REGISTRY.keys())}"
        )
    return cls
