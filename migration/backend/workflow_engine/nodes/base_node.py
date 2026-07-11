"""
Workflow Node Base Class + Execution Context
File: migration/backend/workflow_engine/nodes/base_node.py

Every step in a migration workflow is a WorkflowNode.
Nodes are stateless classes — all state lives in WorkflowContext,
which passes immutably (by copy-on-update) from node to node.

Node contract:
    - Receives a WorkflowContext
    - Does its work (read, transform, validate, write, verify, metrics, notify, audit)
    - Returns an updated WorkflowContext
    - NEVER modifies global state
    - Declares retry_policy and timeout via class attributes (overridable in definition)

Adding a new node type (e.g. DataMaskingNode in Part 8):
    1. Subclass WorkflowNode
    2. Implement execute()
    3. Register: PluginManager.register(PluginType.WORKFLOW_NODE, "mask", DataMaskingNode)
    4. Add to a WorkflowDefinition's nodes list
    Zero changes to existing nodes or the executor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import datetime


# ── Retry Policy ──────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    max_retries:        int   = 3
    backoff_seconds:    float = 5.0
    backoff_multiplier: float = 2.0   # exponential backoff: wait = backoff * multiplier^attempt

    def wait_for_attempt(self, attempt: int) -> float:
        """Returns how many seconds to wait before attempt N (0-indexed)."""
        if attempt <= 0:
            return 0.0
        return self.backoff_seconds * (self.backoff_multiplier ** (attempt - 1))


# ── Workflow Context ──────────────────────────────────────────────────────────

@dataclass
class WorkflowContext:
    """
    The shared state object that flows through every node.
    Each node receives this, does its work, and returns an updated copy.

    Nodes MUST NOT mutate this object — they return a new one
    (or update fields on the received one and return it, since Python
    dataclasses are mutable by default; the executor enforces ordering).
    """
    # ── Job / chunk identification ─────────────────────────────────────────
    job_id:          str
    chunk_id:        str
    table_name:      str
    target_table_name: str          # may differ from table_name due to mapping
    pk_column:       str
    pk_start:        Any
    pk_end:          Any
    worker_id:       str

    # ── Source / target config ─────────────────────────────────────────────
    source_config:   Dict[str, Any] = field(default_factory=dict)
    target_config:   Dict[str, Any] = field(default_factory=dict)

    # ── Column mappings (loaded once by executor, passed to TransformNode) ─
    column_mappings: List[Any] = field(default_factory=list)   # List[ColumnMappingConfig]

    # ── Data payload (populated by ReadNode, consumed by subsequent nodes) ─
    source_rows:     List[Dict[str, Any]] = field(default_factory=list)
    transformed_rows: List[Dict[str, Any]] = field(default_factory=list)
    rows_read:       int = 0
    rows_written:    int = 0
    rows_skipped:    int = 0
    rows_failed:     int = 0

    # ── Validation results (set by ValidateNode and VerifyNode) ───────────
    pre_write_valid: bool = True
    post_write_verified: bool = True
    source_checksum: Optional[str] = None
    target_checksum: Optional[str] = None
    source_row_count: int = 0
    target_row_count: int = 0

    # ── Execution tracking ─────────────────────────────────────────────────
    execution_id:    Optional[str] = None
    started_at:      Optional[datetime.datetime] = None
    current_node:    Optional[str] = None
    completed_nodes: List[str] = field(default_factory=list)
    failed_node:     Optional[str] = None

    # ── Error state ────────────────────────────────────────────────────────
    has_error:       bool = False
    error_message:   Optional[str] = None
    error_node:      Optional[str] = None

    # ── Node-specific scratch space (keyed by node_id) ─────────────────────
    node_results:    Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def mark_error(self, node_id: str, message: str) -> None:
        self.has_error    = True
        self.error_message = message
        self.error_node   = node_id
        self.failed_node  = node_id

    def mark_node_complete(self, node_id: str, result: Dict[str, Any] = None) -> None:
        if node_id not in self.completed_nodes:
            self.completed_nodes.append(node_id)
        self.current_node = None
        if result:
            self.node_results[node_id] = result

    def summary(self) -> Dict[str, Any]:
        """Lightweight summary for DB storage (not the full row payload)."""
        return {
            "job_id":         self.job_id,
            "chunk_id":       self.chunk_id,
            "table_name":     self.table_name,
            "rows_read":      self.rows_read,
            "rows_written":   self.rows_written,
            "rows_skipped":   self.rows_skipped,
            "has_error":      self.has_error,
            "error_message":  self.error_message,
            "error_node":     self.error_node,
            "completed_nodes": self.completed_nodes,
        }


# ── WorkflowNode Base Class ───────────────────────────────────────────────────

class WorkflowNode(ABC):
    """
    Abstract base class for all workflow nodes.

    Subclasses implement execute() and optionally override:
      - node_type:        string identifier used in workflow definitions
      - default_retry:    RetryPolicy used if definition doesn't override
      - default_timeout:  seconds before this node is considered hung

    Nodes are stateless — instantiated fresh per execution by the executor.
    All config is passed via WorkflowContext or the config dict from the
    WorkflowDefinition's node entry.
    """

    node_type: str = "base"
    default_retry: RetryPolicy = RetryPolicy(max_retries=3, backoff_seconds=5)
    default_timeout: int = 300   # seconds

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def execute(self, ctx: WorkflowContext) -> WorkflowContext:
        """
        Execute this node's logic.
        Receives the current context, returns the updated context.
        On failure: set ctx.mark_error(node_id, message) and return ctx.
        Never raise — let the executor handle retry/failure.
        """

    def can_retry(self, ctx: WorkflowContext, attempt: int, retry_policy: RetryPolicy) -> bool:
        """Override to add custom retry logic (e.g. only retry on specific errors)."""
        return attempt < retry_policy.max_retries

    def on_skip(self, ctx: WorkflowContext) -> WorkflowContext:
        """Called when this node is skipped (e.g. upstream failure with condition=on_success)."""
        ctx.mark_node_complete(self.node_type, {"skipped": True})
        return ctx

    def input_summary(self, ctx: WorkflowContext) -> Dict[str, Any]:
        """What to log as input_summary in workflow_node_log. Override for node-specific detail."""
        return {"rows": ctx.rows_read, "table": ctx.table_name}

    def output_summary(self, ctx: WorkflowContext) -> Dict[str, Any]:
        """What to log as output_summary in workflow_node_log."""
        return {"rows_written": ctx.rows_written, "rows_skipped": ctx.rows_skipped}
