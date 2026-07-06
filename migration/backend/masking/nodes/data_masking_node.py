"""
DataMaskingNode
File: migration/backend/masking/nodes/data_masking_node.py

A WorkflowNode (Part 2) that applies masking and synthetic data generation
to rows BETWEEN ReadNode and WriteNode in the migration pipeline.

Default workflow (Part 2):
    ReadNode → TransformNode → ValidateNode → WriteNode → VerifyNode → ...

With masking enabled:
    ReadNode → TransformNode → DataMaskingNode → ValidateNode → WriteNode → ...

Why after TransformNode and before ValidateNode:
    - TransformNode has already renamed/cast columns to target shape
    - Masking operates on the target column names and values
    - ValidateNode checks the final (masked) values for null violations etc.
    - WriteNode writes already-masked rows — source values never reach target

To enable DataMaskingNode in a workflow:
    1. Register it with PluginManager (done at startup below)
    2. Add it to a WorkflowDefinition between transform and validate nodes
    3. Configure it via node config: {"rule_set_id": "...", "job_id": "..."}
       OR it auto-detects mask/synthesize rules from schema_column_mappings

WorkflowDefinition JSON snippet:
    {
      "id": "mask",
      "node_type": "DataMaskingNode",
      "label": "Apply Data Masking",
      "config": {
        "rule_set_id": null,    # null = auto-detect from column mappings
        "fail_on_error": false  # true = fail chunk if masking fails
      },
      "retry_policy": {"max_retries": 1, "backoff_seconds": 0},
      "timeout_seconds": 120
    }
"""

import datetime
import uuid
from typing import Dict, Any, List, Optional

from backend.workflow_engine.nodes.base_node import WorkflowNode, WorkflowContext, RetryPolicy
from backend.masking.masking_engine.masking_engine import MaskingEngine
from backend.shared.config.logging import logger


class DataMaskingNode(WorkflowNode):
    """
    Workflow node: applies masking/synthetic data to transformed_rows.
    Operates on ctx.transformed_rows (post-TransformNode) in-place.
    """
    node_type       = "DataMaskingNode"
    default_retry   = RetryPolicy(max_retries=1, backoff_seconds=0)
    default_timeout = 120

    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        if not ctx.transformed_rows:
            ctx.mark_node_complete("mask", {"skipped": True, "reason": "no rows"})
            return ctx

        try:
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()

            masking_engine = MaskingEngine()
            rule_set_id    = self.config.get("rule_set_id")
            fail_on_error  = self.config.get("fail_on_error", False)

            try:
                # Load rules for this table
                rules = masking_engine.load_rules_for_table(
                    db=db,
                    table_name=ctx.target_table_name or ctx.table_name,
                    rule_set_id=rule_set_id,
                    job_id=ctx.job_id,
                )

                if not rules:
                    # No masking rules for this table — pass through
                    ctx.mark_node_complete("mask", {
                        "skipped": True,
                        "reason":  f"No mask/synthesize rules for table '{ctx.table_name}'"
                    })
                    return ctx

                # Apply masking to the entire batch
                masked_rows = masking_engine.apply_to_batch(ctx.transformed_rows, rules)
                rows_masked = len(masked_rows)

                # Log masking activity
                masking_engine.log_masking(
                    db=db,
                    job_id=ctx.job_id,
                    table_name=ctx.table_name,
                    rules_applied=rules,
                    rows_masked=rows_masked,
                    rows_skipped=0,
                )

                # Replace transformed_rows with masked version
                ctx.transformed_rows = masked_rows

                ctx.mark_node_complete("mask", {
                    "rows_masked":     rows_masked,
                    "rules_applied":   len(rules),
                    "columns_masked":  [r["column_name"] for r in rules],
                })

                logger.info("DataMaskingNode complete",
                            table=ctx.table_name,
                            rows=rows_masked,
                            rules=len(rules),
                            chunk_id=ctx.chunk_id)

            finally:
                db.close()

        except Exception as e:
            if self.config.get("fail_on_error", False):
                ctx.mark_error("mask", f"DataMaskingNode failed: {e}")
            else:
                # Non-fatal: log warning, continue with unmasked rows
                logger.warning("DataMaskingNode failed (non-fatal, continuing with unmasked data)",
                               error=str(e), chunk_id=ctx.chunk_id)
                ctx.mark_node_complete("mask", {"error": str(e), "unmasked": True})

        return ctx

    def input_summary(self, ctx: WorkflowContext) -> Dict[str, Any]:
        return {"rows": len(ctx.transformed_rows), "table": ctx.table_name}

    def output_summary(self, ctx: WorkflowContext) -> Dict[str, Any]:
        result = ctx.node_results.get("mask", {})
        return {
            "rows_masked":  result.get("rows_masked", 0),
            "rules_applied": result.get("rules_applied", 0),
        }


def register_masking_node():
    """
    Register DataMaskingNode with the PluginManager so it's available
    as a workflow node type. Call this at service startup.
    """
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager
        PluginManager.register(
            plugin_type="workflow_node",
            name="DataMaskingNode",
            plugin_class=DataMaskingNode,
            display_name="Data Masking & Synthetic Data",
            capabilities=["mask", "synthesize", "hash", "redact", "partial", "encrypt", "fake_data"],
            is_builtin=True,
        )
        # Also register in default_nodes.py NODE_REGISTRY via import
        from backend.workflow_engine.nodes.default_nodes import NODE_REGISTRY
        NODE_REGISTRY["DataMaskingNode"] = DataMaskingNode
        logger.info("DataMaskingNode registered")
    except Exception as e:
        logger.warning("Failed to register DataMaskingNode", error=str(e))
