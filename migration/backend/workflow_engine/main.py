"""
Workflow Engine Service — FastAPI Application
File: migration/backend/workflow_engine/main.py

Part 2: Workflow Engine as Execution Kernel. Runs on port 8008.

This service owns workflow definitions and execution history.
The Worker process (worker.py) does not run as a FastAPI service —
it runs as a standalone Python process pulling from Redis.
This FastAPI service exposes the management/inspection API for workflows.

Start the API:
    cd migration/
    uvicorn backend.workflow_engine.main:app --host 0.0.0.0 --port 8008 --reload

Start a Worker:
    cd migration/
    WORKER_ID=worker-1 python -m backend.workflow_engine.worker

Start multiple Workers:
    WORKER_ID=worker-1 python -m backend.workflow_engine.worker &
    WORKER_ID=worker-2 python -m backend.workflow_engine.worker &
    WORKER_ID=worker-3 python -m backend.workflow_engine.worker &

Docs: http://localhost:8008/docs

ALL ENDPOINTS:

── WORKFLOW DEFINITIONS ──────────────────────────────────────────────────────
    POST   /workflows                       Create a custom workflow definition
    GET    /workflows                       List all workflow definitions
    GET    /workflows/default               Get the default workflow
    GET    /workflows/{id}                  Get one workflow definition
    PUT    /workflows/{id}/set-default      Set as default for new jobs
    DELETE /workflows/{id}                  Deactivate a workflow

── WORKFLOW EXECUTIONS ───────────────────────────────────────────────────────
    GET    /jobs/{job_id}/executions        All executions for a job
    GET    /executions/{id}                 One execution detail
    GET    /executions/{id}/node-log        Per-node execution log

How the Workflow Engine replaces ChunkExecutor:

    OLD FLOW:
        Worker → Redis BRPOP → ChunkExecutor.execute()
            └── hardcoded: read → transform → write → validate → metrics

    NEW FLOW:
        Worker → Redis BRPOP → WorkflowExecutor.execute()
            └── loads WorkflowDefinition from DB
            └── executes DAG: ReadNode → TransformNode → ValidateNode →
                               WriteNode → VerifyNode → MetricsNode →
                               NotifyNode → AuditNode
            └── each node is independently retryable, timeout-guarded,
                logged to workflow_node_log, and extendable via PluginManager

The default 'standard_migration' workflow (seeded in 007_workflow_engine.sql)
produces identical behavior to the old ChunkExecutor — zero regression.
Custom workflows are purely additive.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.workflow_engine.routers import workflows

app = FastAPI(
    title="Migration Platform — Workflow Engine",
    description=(
        "Part 2: Workflow Engine as Execution Kernel. "
        "Manages workflow definitions (serializable DAGs of typed nodes) "
        "and execution history. Workers load workflow definitions and "
        "execute them node-by-node with per-node retry, timeout, and "
        "logging. Replaces the monolithic ChunkExecutor with a composable, "
        "extensible, customer-configurable migration pipeline."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router)


@app.on_event("startup")
def on_startup():
    """Register with Service Registry and sync nodes to Plugin Catalog."""
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        # Register with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="workflow_engine",
                display_name="Workflow Engine",
                base_url="http://localhost:8008",
                version="1.0.0",
                metadata={"part": 2, "default_nodes": [
                    "ReadNode", "TransformNode", "ValidateNode", "WriteNode",
                    "VerifyNode", "MetricsNode", "NotifyNode", "AuditNode"
                ]},
            )
        except Exception as e:
            logger.warning("Could not register with Service Registry", error=str(e))

        # Register built-in node types with PluginManager
        try:
            from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
            from backend.workflow_engine.nodes.default_nodes import NODE_REGISTRY
            for node_type, node_class in NODE_REGISTRY.items():
                PluginManager.register(
                    plugin_type="workflow_node",
                    name=node_type,
                    plugin_class=node_class,
                    display_name=node_type,
                    is_builtin=True,
                )
            PluginManager.sync_to_catalog(db)
        except Exception as e:
            logger.warning("Could not register nodes with PluginManager", error=str(e))

        logger.info("Workflow Engine started", port=8008)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "workflow_engine",
        "port":    8008,
        "version": "1.0.0",
        "default_nodes": [
            "ReadNode", "TransformNode", "ValidateNode", "WriteNode",
            "VerifyNode", "MetricsNode", "NotifyNode", "AuditNode"
        ],
    }
