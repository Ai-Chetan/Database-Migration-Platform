"""
Workflow Engine Router
File: migration/backend/workflow_engine/routers/workflows.py

Endpoints:
    POST /workflows                         → create a workflow definition
    GET  /workflows                         → list workflow definitions
    GET  /workflows/{id}                    → get workflow definition
    GET  /workflows/default                 → get the default workflow
    PUT  /workflows/{id}/set-default        → set a workflow as default
    DELETE /workflows/{id}                  → deactivate a workflow
    GET  /workflows/{id}/executions         → list executions for a definition
    GET  /executions/{id}                   → get one execution detail
    GET  /executions/{id}/node-log          → per-node execution log
    GET  /jobs/{job_id}/executions          → all executions for a job
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.workflow_engine.definition.workflow_definition import (
    WorkflowDefinitionBuilder, WorkflowDefinitionRepository
)

router = APIRouter(tags=["Workflow Engine"])
repo   = WorkflowDefinitionRepository()


class NodeDef(BaseModel):
    id:              str
    node_type:       str
    label:           str
    config:          Optional[Dict[str, Any]] = None
    retry_policy:    Optional[Dict[str, Any]] = None
    timeout_seconds: int  = 300
    parallelizable:  bool = False


class EdgeDef(BaseModel):
    from_node:  str
    to_node:    str
    condition:  str = "on_success"


class CreateWorkflowRequest(BaseModel):
    name:        str
    version:     str = "1.0.0"
    description: str = ""
    tenant_id:   str = "local"
    nodes:       List[NodeDef]
    edges:       List[EdgeDef]
    is_default:  bool = False


@router.post("/workflows", summary="Create a workflow definition")
def create_workflow(req: CreateWorkflowRequest, db: Session = Depends(get_db)):
    """
    Create a custom workflow pipeline.

    The workflow is a DAG of nodes with typed edges:
      - on_success: follow this edge only if the previous node succeeded
      - on_failure: follow this edge only if the previous node failed
      - always:     always follow this edge regardless of previous node result

    Built-in node types (Part 2):
      ReadNode, TransformNode, ValidateNode, WriteNode, VerifyNode,
      MetricsNode, NotifyNode, AuditNode

    Future node types (registered via PluginManager):
      DataMaskingNode (Part 8), DataProfilingNode (Part 4), etc.

    Example custom workflow (skip verify for speed on non-critical tables):
    {
      "name": "fast_migration",
      "nodes": [
        {"id": "read",    "node_type": "ReadNode",      "label": "Read"},
        {"id": "write",   "node_type": "WriteNode",     "label": "Write"},
        {"id": "metrics", "node_type": "MetricsNode",   "label": "Metrics"},
        {"id": "audit",   "node_type": "AuditNode",     "label": "Audit"}
      ],
      "edges": [
        {"from_node": "read",    "to_node": "write",   "condition": "on_success"},
        {"from_node": "write",   "to_node": "metrics", "condition": "always"},
        {"from_node": "metrics", "to_node": "audit",   "condition": "always"}
      ]
    }
    """
    builder = WorkflowDefinitionBuilder(
        name=req.name, version=req.version, tenant_id=req.tenant_id
    )
    for node in req.nodes:
        builder.add_node(
            node_id=node.id, node_type=node.node_type, label=node.label,
            config=node.config, retry_policy=node.retry_policy,
            timeout_seconds=node.timeout_seconds, parallelizable=node.parallelizable,
        )
    for edge in req.edges:
        builder.add_edge(edge.from_node, edge.to_node, edge.condition)
    if req.is_default:
        builder.set_default()

    definition = builder.build()
    return repo.save(db, definition)


@router.get("/workflows", summary="List all workflow definitions")
def list_workflows(tenant_id: str = "local", db: Session = Depends(get_db)):
    return repo.list_all(db, tenant_id)


@router.get("/workflows/default", summary="Get the default workflow definition")
def get_default_workflow(tenant_id: str = "local", db: Session = Depends(get_db)):
    row = repo.get_default(db, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="No default workflow found. Run the DB migration (007_workflow_engine.sql).")
    return row


@router.get("/workflows/{definition_id}", summary="Get a workflow definition")
def get_workflow(definition_id: str, db: Session = Depends(get_db)):
    row = repo.get_by_id(db, definition_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Workflow {definition_id} not found")
    return row


@router.put("/workflows/{definition_id}/set-default",
            summary="Set a workflow as the default for all new jobs")
def set_default(definition_id: str, tenant_id: str = "local", db: Session = Depends(get_db)):
    row = repo.get_by_id(db, definition_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Workflow {definition_id} not found")

    # Unset any existing default for this tenant
    db.execute(
        text("UPDATE workflow_definitions SET is_default=FALSE WHERE tenant_id=:tid AND id!=:id"),
        {"tid": tenant_id, "id": definition_id}
    )
    db.execute(
        text("UPDATE workflow_definitions SET is_default=TRUE WHERE id=:id"),
        {"id": definition_id}
    )
    db.commit()
    return {"definition_id": definition_id, "is_default": True}


@router.delete("/workflows/{definition_id}", summary="Deactivate a workflow definition")
def deactivate_workflow(definition_id: str, db: Session = Depends(get_db)):
    row = repo.get_by_id(db, definition_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Workflow {definition_id} not found")
    if row.get("is_default"):
        raise HTTPException(status_code=400, detail="Cannot deactivate the default workflow. Set another workflow as default first.")
    db.execute(
        text("UPDATE workflow_definitions SET is_active=FALSE WHERE id=:id"),
        {"id": definition_id}
    )
    db.commit()
    return {"definition_id": definition_id, "is_active": False}


@router.get("/jobs/{job_id}/executions", summary="List all workflow executions for a job")
def list_job_executions(job_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, chunk_id, worker_id, status, started_at, completed_at,
                   duration_ms, rows_read, rows_written, rows_skipped,
                   current_node, error_message, error_node, retry_count
            FROM workflow_executions
            WHERE job_id = :jid
            ORDER BY started_at DESC
        """),
        {"jid": job_id}
    ).fetchall()

    executions = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        executions.append(d)

    total        = len(executions)
    completed    = sum(1 for e in executions if e["status"] == "completed")
    failed       = sum(1 for e in executions if e["status"] == "failed")
    total_rows   = sum(e.get("rows_written") or 0 for e in executions)

    return {
        "job_id":      job_id,
        "total":       total,
        "completed":   completed,
        "failed":      failed,
        "total_rows_written": total_rows,
        "executions":  executions,
    }


@router.get("/executions/{execution_id}", summary="Get one workflow execution detail")
def get_execution(execution_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM workflow_executions WHERE id=:id"),
        {"id": execution_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Execution {execution_id} not found")
    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d


@router.get("/executions/{execution_id}/node-log",
            summary="Per-node execution log for a workflow execution")
def get_node_log(execution_id: str, db: Session = Depends(get_db)):
    """
    Returns the per-node execution log for one workflow execution.
    Shows exactly which node ran, when, for how long, and what it produced.
    This is the "what happened inside this chunk" view.
    """
    rows = db.execute(
        text("""
            SELECT id, node_id, node_type, status, started_at, completed_at,
                   duration_ms, input_summary, output_summary, error_message, retry_count
            FROM workflow_node_log
            WHERE execution_id = :eid
            ORDER BY started_at ASC
        """),
        {"eid": execution_id}
    ).fetchall()

    nodes = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        nodes.append(d)

    return {
        "execution_id": execution_id,
        "node_count":   len(nodes),
        "nodes":        nodes,
    }
