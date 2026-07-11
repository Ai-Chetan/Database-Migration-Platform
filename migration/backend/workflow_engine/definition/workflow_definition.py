"""
Workflow Definition
File: migration/backend/workflow_engine/definition/workflow_definition.py

A WorkflowDefinition is a serializable description of the migration pipeline.
It's a DAG of nodes with typed edges (always / on_success / on_failure).

Stored in workflow_definitions table as JSON.
Loaded by WorkflowExecutor before running.
Can be created, versioned, and shared as Templates (Part 9).

The WorkflowDefinitionBuilder provides a fluent API for constructing
definitions programmatically without raw JSON.
"""

import json
import datetime
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.shared.config.logging import logger


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class NodeDefinition:
    """One node in the workflow DAG."""
    id:               str            # unique within this workflow, e.g. "read", "write"
    node_type:        str            # maps to a WorkflowNode subclass, e.g. "ReadNode"
    label:            str            # human-readable, shown in UI
    config:           Dict[str, Any] = field(default_factory=dict)
    retry_policy:     Dict[str, Any] = field(default_factory=lambda: {
        "max_retries": 3, "backoff_seconds": 5, "backoff_multiplier": 2
    })
    timeout_seconds:  int  = 300
    parallelizable:   bool = False   # can this node run in parallel across chunks?

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "node_type":       self.node_type,
            "label":           self.label,
            "config":          self.config,
            "retry_policy":    self.retry_policy,
            "timeout_seconds": self.timeout_seconds,
            "parallelizable":  self.parallelizable,
        }


@dataclass
class EdgeDefinition:
    """A directed edge from one node to another with a condition."""
    from_node:  str
    to_node:    str
    condition:  str = "on_success"   # always | on_success | on_failure

    def to_dict(self) -> dict:
        return {"from": self.from_node, "to": self.to_node, "condition": self.condition}


@dataclass
class WorkflowDefinition:
    """The complete DAG definition for a migration pipeline."""
    name:        str
    version:     str = "1.0.0"
    description: str = ""
    tenant_id:   str = "global"
    nodes:       List[NodeDefinition] = field(default_factory=list)
    edges:       List[EdgeDefinition] = field(default_factory=list)
    db_id:       Optional[str] = None
    is_default:  bool = False

    def node_by_id(self, node_id: str) -> Optional[NodeDefinition]:
        return next((n for n in self.nodes if n.id == node_id), None)

    def successors(self, node_id: str, on_success: bool = True) -> List[str]:
        """Get IDs of nodes that come after node_id given the execution result."""
        result = []
        for edge in self.edges:
            if edge.from_node != node_id:
                continue
            if edge.condition == "always":
                result.append(edge.to_node)
            elif edge.condition == "on_success" and on_success:
                result.append(edge.to_node)
            elif edge.condition == "on_failure" and not on_success:
                result.append(edge.to_node)
        return result

    def entry_nodes(self) -> List[str]:
        """Nodes with no incoming edges — execution starts here."""
        has_incoming = {e.to_node for e in self.edges}
        return [n.id for n in self.nodes if n.id not in has_incoming]

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "version":     self.version,
            "description": self.description,
            "nodes":       [n.to_dict() for n in self.nodes],
            "edges":       [e.to_dict() for e in self.edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── DB persistence ────────────────────────────────────────────────────────────

class WorkflowDefinitionRepository:

    def save(self, db: Session, definition: WorkflowDefinition) -> dict:
        """Save (upsert by name+version) a workflow definition."""
        did = str(uuid.uuid4())
        now = datetime.datetime.utcnow()

        db.execute(
            text("""
                INSERT INTO workflow_definitions
                    (id, tenant_id, name, version, description,
                     nodes, edges, is_default, is_active, created_at, updated_at)
                VALUES
                    (:id, :tid, :name, :ver, :desc,
                     :nodes::jsonb, :edges::jsonb, :is_def, TRUE, :now, :now)
                ON CONFLICT (tenant_id, name, version)
                DO UPDATE SET
                    description = :desc,
                    nodes       = :nodes::jsonb,
                    edges       = :edges::jsonb,
                    is_default  = :is_def,
                    updated_at  = :now
                RETURNING id
            """),
            {
                "id":     did,
                "tid":    definition.tenant_id,
                "name":   definition.name,
                "ver":    definition.version,
                "desc":   definition.description,
                "nodes":  json.dumps([n.to_dict() for n in definition.nodes]),
                "edges":  json.dumps([e.to_dict() for e in definition.edges]),
                "is_def": definition.is_default,
                "now":    now,
            }
        )
        db.commit()
        return self.get_by_name(db, definition.tenant_id, definition.name, definition.version)

    def get_by_name(
        self, db: Session, tenant_id: str, name: str, version: str = None
    ) -> Optional[dict]:
        if version:
            row = db.execute(
                text("SELECT * FROM workflow_definitions WHERE tenant_id=:tid AND name=:name AND version=:ver"),
                {"tid": tenant_id, "name": name, "ver": version}
            ).fetchone()
        else:
            row = db.execute(
                text("SELECT * FROM workflow_definitions WHERE tenant_id=:tid AND name=:name ORDER BY created_at DESC LIMIT 1"),
                {"tid": tenant_id, "name": name}
            ).fetchone()
        return self._row(row) if row else None

    def get_default(self, db: Session, tenant_id: str = "global") -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM workflow_definitions WHERE (tenant_id=:tid OR tenant_id='global') AND is_default=TRUE ORDER BY CASE WHEN tenant_id=:tid THEN 0 ELSE 1 END LIMIT 1"),
            {"tid": tenant_id}
        ).fetchone()
        return self._row(row) if row else None

    def get_by_id(self, db: Session, definition_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM workflow_definitions WHERE id=:id"),
            {"id": definition_id}
        ).fetchone()
        return self._row(row) if row else None

    def list_all(self, db: Session, tenant_id: str = "local") -> list:
        rows = db.execute(
            text("SELECT * FROM workflow_definitions WHERE (tenant_id=:tid OR tenant_id='global') AND is_active=TRUE ORDER BY name, version DESC"),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def from_db_row(self, row: dict) -> WorkflowDefinition:
        """Reconstruct a WorkflowDefinition object from a DB row dict."""
        nodes_data = row.get("nodes", [])
        edges_data = row.get("edges", [])
        if isinstance(nodes_data, str):
            nodes_data = json.loads(nodes_data)
        if isinstance(edges_data, str):
            edges_data = json.loads(edges_data)

        nodes = [
            NodeDefinition(
                id=n["id"], node_type=n["node_type"], label=n["label"],
                config=n.get("config", {}), retry_policy=n.get("retry_policy", {}),
                timeout_seconds=n.get("timeout_seconds", 300),
                parallelizable=n.get("parallelizable", False),
            )
            for n in nodes_data
        ]
        edges = [
            EdgeDefinition(from_node=e["from"], to_node=e["to"],
                          condition=e.get("condition", "on_success"))
            for e in edges_data
        ]

        return WorkflowDefinition(
            name=row["name"], version=row.get("version", "1.0.0"),
            description=row.get("description", ""),
            tenant_id=row.get("tenant_id", "global"),
            nodes=nodes, edges=edges,
            db_id=str(row.get("id", "")),
            is_default=row.get("is_default", False),
        )

    def _row(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d


# ── Fluent builder ────────────────────────────────────────────────────────────

class WorkflowDefinitionBuilder:
    """
    Fluent API for building WorkflowDefinitions programmatically.

    Usage:
        definition = (WorkflowDefinitionBuilder("custom_workflow")
            .add_node("read",      "ReadNode",      "Read Source")
            .add_node("transform", "TransformNode", "Transform")
            .add_node("write",     "WriteNode",     "Write Target",
                      config={"mode": "upsert"})
            .chain(["read", "transform", "write"])
            .build())
    """

    def __init__(self, name: str, version: str = "1.0.0", tenant_id: str = "global"):
        self._definition = WorkflowDefinition(
            name=name, version=version, tenant_id=tenant_id
        )

    def add_node(
        self, node_id: str, node_type: str, label: str,
        config: Dict[str, Any] = None,
        retry_policy: Dict[str, Any] = None,
        timeout_seconds: int = 300,
        parallelizable: bool = False,
    ) -> "WorkflowDefinitionBuilder":
        self._definition.nodes.append(NodeDefinition(
            id=node_id, node_type=node_type, label=label,
            config=config or {}, retry_policy=retry_policy or {},
            timeout_seconds=timeout_seconds, parallelizable=parallelizable,
        ))
        return self

    def add_edge(
        self, from_node: str, to_node: str, condition: str = "on_success"
    ) -> "WorkflowDefinitionBuilder":
        self._definition.edges.append(EdgeDefinition(from_node, to_node, condition))
        return self

    def chain(self, node_ids: List[str], condition: str = "on_success") -> "WorkflowDefinitionBuilder":
        """Connect a list of nodes in sequence: a→b→c→..."""
        for i in range(len(node_ids) - 1):
            self.add_edge(node_ids[i], node_ids[i+1], condition)
        return self

    def set_default(self, is_default: bool = True) -> "WorkflowDefinitionBuilder":
        self._definition.is_default = is_default
        return self

    def build(self) -> WorkflowDefinition:
        return self._definition
