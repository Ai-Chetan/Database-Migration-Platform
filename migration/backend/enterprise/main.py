"""
Enterprise Execution Engine — FastAPI Application
File: migration/backend/enterprise/main.py

Phase 10 microservice. Runs on port 8004.

Start:
    cd migration/
    uvicorn backend.enterprise.main:app --host 0.0.0.0 --port 8004 --reload

Docs: http://localhost:8004/docs

ALL ENDPOINTS:

── CONNECTION MANAGER ────────────────────────────────────────────────────────
    POST   /connections                     Register new DB connection (encrypted)
    GET    /connections                     List all connections (no passwords)
    GET    /connections/{id}                Get one connection
    POST   /connections/{id}/test           Test existing connection
    POST   /connections/test-raw            Test without saving
    PUT    /connections/{id}/rotate         Rotate password
    DELETE /connections/{id}                Deactivate connection

── ADAPTIVE CHUNK PLANNING ───────────────────────────────────────────────────
    POST   /jobs/{id}/plan-chunks           Compute adaptive chunk sizes for all tables
    POST   /jobs/{id}/plan-chunks/{table}   Compute for one table
    GET    /jobs/{id}/chunk-plans           Get saved chunk plans

── DEPENDENCY GRAPH ──────────────────────────────────────────────────────────
    POST   /jobs/{id}/dependency-graph      Build FK dependency graph
    GET    /jobs/{id}/dependency-graph      Get saved graph
    GET    /jobs/{id}/dependency-graph/order  Execution order in plain English

── RESOURCE GOVERNOR ─────────────────────────────────────────────────────────
    POST   /jobs/{id}/governor/start        Start DB load monitoring
    POST   /jobs/{id}/governor/stop         Stop monitoring + clear throttle
    GET    /jobs/{id}/governor/status       Current throttle state
    GET    /jobs/{id}/governor/history      Historical readings

── ROLLBACK ENGINE ───────────────────────────────────────────────────────────
    POST   /jobs/{id}/rollback/generate     Generate rollback plan
    GET    /jobs/{id}/rollback/plan         Get saved plan
    POST   /jobs/{id}/rollback/dry-run      Preview without executing
    POST   /jobs/{id}/rollback/execute      Execute rollback (IRREVERSIBLE)
    GET    /jobs/{id}/rollback/log          Step-by-step execution log
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.enterprise.routers import (
    connections,
    chunk_planning,
    dependency_graph,
    resource_governor,
    rollback,
)

app = FastAPI(
    title="Migration Platform — Enterprise Execution Engine",
    description=(
        "Phase 10: Production-grade migration execution. "
        "Encrypted connection registry, adaptive chunk sizing, "
        "FK-aware dependency graph execution, DB load monitoring "
        "with auto-throttling, and full rollback support."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(connections.router)
app.include_router(chunk_planning.router)
app.include_router(dependency_graph.router)
app.include_router(resource_governor.router)
app.include_router(rollback.router)


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "enterprise_execution_engine",
        "port":    8004,
        "version": "1.0.0",
    }
