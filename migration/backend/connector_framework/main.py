"""
Connector Framework + CDC Engine — FastAPI Application
File: migration/backend/connector_framework/main.py

Runs on port 8006.

Start:
    cd migration/
    uvicorn backend.connector_framework.main:app --host 0.0.0.0 --port 8006 --reload

Docs: http://localhost:8006/docs

Environment variables (none new required — uses existing DB config)

ALL ENDPOINTS:

── CONNECTOR FRAMEWORK ───────────────────────────────────────────────────────
    GET  /connectors                    List all registered connector plugins
    GET  /connectors/{name}             Get connector detail + capabilities
    POST /connectors/test               Test a connection config
    POST /connectors/validate-config    Validate config fields for a connector
    POST /connectors/discover           Discover schema via connector plugin

── CDC ENGINE ─────────────────────────────────────────────────────────────────
    POST /cdc/sessions                       Create CDC session for a job
    GET  /cdc/sessions/{id}                  Get session status
    POST /cdc/sessions/{id}/start            Start capturing changes
    POST /cdc/sessions/{id}/stop             Stop capture
    GET  /cdc/sessions/{id}/stats            Events captured/replayed/pending
    GET  /cdc/sessions/{id}/lag              Current replication lag
    POST /cdc/sessions/{id}/replay           Replay all pending events
    POST /cdc/sessions/{id}/replay-until-lag Replay until lag < threshold
    POST /cdc/sessions/{id}/cutover          Execute cutover (manual/automated)
    POST /cdc/sessions/{id}/complete         Complete manual cutover
    GET  /cdc/sessions/{id}/cutover-log      Step-by-step cutover log

── POLICY ENGINE ──────────────────────────────────────────────────────────────
    POST   /policies                    Create organizational policy
    GET    /policies                    List policies for tenant
    GET    /policies/{id}               Get policy detail
    PUT    /policies/{id}/toggle        Enable/disable policy
    DELETE /policies/{id}               Delete policy
    POST   /policies/check/{job_id}     Run all policy checks against a job
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.connector_framework.routers import connectors, cdc, policy

app = FastAPI(
    title="Migration Platform — Connector Framework & CDC Engine",
    description=(
        "Plugin-based database connector framework with a unified interface "
        "(discover, stream, bulk_write, checksum, CDC). Built-in MySQL, "
        "PostgreSQL, and SQLite connectors. Change Data Capture (CDC) engine "
        "for near-zero-downtime migrations: capture → replay → cutover. "
        "Plus a policy engine enforcing organizational migration rules."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(connectors.router)
app.include_router(cdc.router)
app.include_router(policy.router)


@app.get("/health", tags=["Health"])
def health():
    from backend.connector_framework.registry.connector_registry import ConnectorRegistry
    return {
        "status":     "ok",
        "service":    "connector_framework_cdc",
        "port":       8006,
        "version":    "1.0.0",
        "connectors": [c["name"] for c in ConnectorRegistry.list_connectors()],
    }
