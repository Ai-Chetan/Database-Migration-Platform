"""
Migration Platform Kernel — FastAPI Application
File: migration/backend/kernel/main.py

Part 1: Kernel Foundation. Runs on port 8007.

This is the permanent core service of the Migration Platform Kernel
architecture. Everything else (connectors, validators, transformers,
notifiers, assessment, scheduler, policy, report, ai, storage, security,
monitoring plugins — and every microservice built so far) sits on top
of the four primitives this service exposes:

    1. Plugin Manager    — universal registry for all plugin types
    2. Event Bus          — pub/sub so services react without direct coupling
    3. Service Registry   — service discovery, replaces hardcoded ports
    4. Metadata Catalog    — the contract Part 3 (Metadata Intelligence Layer)
                             will populate and everything downstream will read

Start:
    cd migration/
    uvicorn backend.kernel.main:app --host 0.0.0.0 --port 8007 --reload

Docs: http://localhost:8007/docs

On startup this service:
    - Registers itself in the Service Registry
    - Starts the background health-checker for all registered services

ALL ENDPOINTS:

── PLUGIN MANAGER ────────────────────────────────────────────────────────────
    GET  /plugins                       List in-memory registered plugins
    GET  /plugins/{type}                List plugins of one type
    POST /plugins/sync                  Push in-memory → persistent catalog
    GET  /plugins/catalog/all           Query persistent plugin catalog
    GET  /plugins/catalog/{type}        Query catalog filtered by type

── EVENT BUS ──────────────────────────────────────────────────────────────────
    POST /events/publish                Publish an event
    GET  /events/timeline/{corr_id}     Full event history for a job/resource
    GET  /events/replay                 Replay events since a timestamp
    POST /events/subscriptions          Register a subscription (introspection)
    GET  /events/subscriptions          List subscriptions
    GET  /events/types                  Distinct event types seen + counts

── SERVICE REGISTRY ───────────────────────────────────────────────────────────
    POST   /services                       Register a microservice
    GET    /services                       List all services
    GET    /services/{name}                Get one service's detail
    GET    /services/{name}/url            Get just the base URL
    POST   /services/{name}/health-check   Check health now
    POST   /services/health-check-all      Check health of every service
    DELETE /services/{name}                Deregister a service

── METADATA CATALOG ───────────────────────────────────────────────────────────
    POST   /catalog/write                          Write a catalog entry
    POST   /catalog/write-bulk                      Bulk write for a connection
    GET    /catalog/{table}/{type}                  Get latest entry
    GET    /catalog/{table}                         Get ALL types for a table
    GET    /catalog/{table}/{type}/history          Historical entries
    GET    /catalog/stale/list                      Entries needing refresh
    DELETE /catalog/{table}                         Delete all entries for table
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.kernel.routers import plugins, events, services, catalog
from backend.kernel.service_registry.service_registry import ServiceRegistry
from backend.shared.config.logging import logger

app = FastAPI(
    title="Migration Platform Kernel",
    description=(
        "The permanent core of the Migration Platform. "
        "Plugin Manager (universal registry for connectors, validators, "
        "transformers, notifiers, and every other plugin type), Event Bus "
        "(Redis Pub/Sub + durable log for cross-service reactions), "
        "Service Registry (discovery for all microservices), and Metadata "
        "Catalog (the contract for table-level intelligence used by "
        "Assessment, Advisor, Cost Estimator, Adaptive Chunk Planner, and "
        "the Workflow Engine)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plugins.router)
app.include_router(events.router)
app.include_router(services.router)
app.include_router(catalog.router)


@app.on_event("startup")
def on_startup():
    """Register this service and start the platform-wide health checker."""
    from backend.shared.config.database import SessionLocal
    db = SessionLocal()
    try:
        ServiceRegistry.register(
            db=db,
            service_name="platform_kernel",
            display_name="Migration Platform Kernel",
            base_url="http://localhost:8007",
            version="1.0.0",
            metadata={"part": 1, "components": ["plugin_manager", "event_bus",
                                                  "service_registry", "metadata_catalog"]},
        )
        ServiceRegistry.start_health_checker(interval_seconds=30)
        logger.info("Migration Platform Kernel started", port=8007)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "platform_kernel",
        "port":    8007,
        "version": "1.0.0",
        "components": ["plugin_manager", "event_bus", "service_registry", "metadata_catalog"],
    }
