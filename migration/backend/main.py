"""
Migration Platform Kernel — Unified Main Entry Point
File: migration/backend/main.py

Runs ALL platform services on port 8000.
This replaces running 15 individual FastAPI services on separate ports.

Start:
    cd migration/
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

Also start (separate processes — they run background threads):
    uvicorn backend.connector_framework.main:app --host 0.0.0.0 --port 8006 --reload
    uvicorn backend.monitoring_service.app.main:app --host 0.0.0.0 --port 8001 --reload

Workers (no HTTP port — start as many as needed):
    WORKER_ID=worker-1 python -m backend.worker_service.app.worker
    WORKER_ID=worker-2 python -m backend.worker_service.app.worker

Docs: http://localhost:8000/docs

API sections available at port 8000:
    /jobs, /tables, /chunks, /connections    → Control Plane
    /projects, /schemas, /mappings           → Schema Mapping
    /auth, /tenants, /users, /approvals      → Security + SaaS
    /plugins, /validators, /policies         → Plugin Service
    /catalog, /events, /services             → Kernel
    /workflows, /executions                  → Workflow Engine
    /intelligence, /scans                    → Metadata Intelligence
    /assess, /advise, /estimate, /quality    → Intelligence Service
    /simulate                                → Simulation Engine
    /masking, /rule-sets                     → Data Masking
    /connectors/extended                     → Extended Connectors
    /ops                                     → Operations Console
    /scheduler, /reports, /knowledge         → Scheduler + Reporting + KB
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Migration Platform Kernel",
    description=(
        "Enterprise-grade database migration platform. "
        "Unified API endpoint for all platform capabilities: "
        "job management, schema mapping, intelligence analysis, "
        "simulation, masking, operations console, scheduling, and reporting."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Import and register all routers ───────────────────────────────────────────
# Each import is wrapped in try/except so one missing module doesn't
# prevent the rest of the platform from starting.

def _include(router, prefix="", tags=None):
    """Safe router inclusion — logs warning on import failure."""
    try:
        app.include_router(router)
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").warning(f"Router include failed: {e}")


# ── 1. Control Plane ──────────────────────────────────────────────────────────
try:
    from backend.control_plane.app.routers import jobs, planning, connections
    app.include_router(jobs.router)
    app.include_router(planning.router)
    app.include_router(connections.router)
except Exception as e:
    print(f"[WARN] Control Plane routers not loaded: {e}")

# ── 2. Schema Mapping Service ─────────────────────────────────────────────────
try:
    from backend.schema_mapping_service.app.routers import (
        schema_discovery, schema_comparison, mapping_projects,
        column_mappings, validation, dry_run
    )
    app.include_router(schema_discovery.router)
    app.include_router(schema_comparison.router)
    app.include_router(mapping_projects.router)
    app.include_router(column_mappings.router)
    app.include_router(validation.router)
    app.include_router(dry_run.router)
except Exception as e:
    print(f"[WARN] Schema Mapping routers not loaded: {e}")

# ── 3. Enterprise Security + SaaS ─────────────────────────────────────────────
try:
    from backend.enterprise.routers import auth, tenants, approvals, templates, audit, secrets
    app.include_router(auth.router)
    app.include_router(tenants.router)
    app.include_router(approvals.router)
    app.include_router(templates.router)
    app.include_router(audit.router)
    app.include_router(secrets.router)
except Exception as e:
    print(f"[WARN] Security routers not loaded: {e}")

# ── 4. Platform Kernel (Plugin Manager, Event Bus, Service Registry, Catalog) ─
try:
    from backend.kernel.routers import plugins, events, services, catalog
    app.include_router(plugins.router)
    app.include_router(events.router)
    app.include_router(services.router)
    app.include_router(catalog.router)
except Exception as e:
    print(f"[WARN] Kernel routers not loaded: {e}")

# ── 5. Workflow Engine ────────────────────────────────────────────────────────
try:
    from backend.workflow_engine.routers import workflows
    app.include_router(workflows.router)
except Exception as e:
    print(f"[WARN] Workflow Engine router not loaded: {e}")

# ── 6. Metadata Intelligence Layer (Part 3) ───────────────────────────────────
try:
    from backend.intelligence.routers import intelligence as intel_scan
    app.include_router(intel_scan.router)
except Exception as e:
    print(f"[WARN] Intelligence Layer router not loaded: {e}")

# ── 7. Intelligence Service (Assessment, Advisor, Estimator, Scanner) ─────────
try:
    from backend.intelligence_service.routers import intelligence_service
    app.include_router(intelligence_service.router)
except Exception as e:
    print(f"[WARN] Intelligence Service router not loaded: {e}")

# ── 8. Simulation Engine ──────────────────────────────────────────────────────
try:
    from backend.simulation.routers import simulation
    app.include_router(simulation.router)
except Exception as e:
    print(f"[WARN] Simulation Engine router not loaded: {e}")

# ── 9. Data Masking + Synthetic Data ─────────────────────────────────────────
try:
    from backend.masking.routers import masking
    app.include_router(masking.router)
except Exception as e:
    print(f"[WARN] Masking router not loaded: {e}")

# ── 10. Plugin Refactor (Validators, Transformers, Notifiers, Policies) ───────
try:
    from backend.plugins.routers import plugins as plugin_router
    app.include_router(plugin_router.router)
except Exception as e:
    print(f"[WARN] Plugin Service router not loaded: {e}")

# ── 11. Extended Connectors (File, S3, REST API, Kafka) ──────────────────────
try:
    from backend.connectors.routers import extended_connectors
    app.include_router(extended_connectors.router)
except Exception as e:
    print(f"[WARN] Extended Connectors router not loaded: {e}")

# ── 12. Operations Console ────────────────────────────────────────────────────
try:
    from backend.operations.routers import operations
    app.include_router(operations.router)
except Exception as e:
    print(f"[WARN] Operations Console router not loaded: {e}")

# ── 13. Scheduler + Reporting + Knowledge Base ────────────────────────────────
try:
    from backend.scheduler.routers import scheduler_reporting_kb
    app.include_router(scheduler_reporting_kb.router)
except Exception as e:
    print(f"[WARN] Scheduler/Reporting/KB router not loaded: {e}")

# ── 14. Monitoring (basic endpoints — full metrics on port 8001) ──────────────
try:
    from backend.monitoring_service.app.routers import monitoring
    app.include_router(monitoring.router)
except Exception as e:
    print(f"[WARN] Monitoring router not loaded: {e}")

# ── 15. Connector Framework public endpoints (test/validate — CDC stays 8006) ─
try:
    from backend.connector_framework.routers import connectors
    app.include_router(connectors.router)
except Exception as e:
    print(f"[WARN] Connector Framework router not loaded: {e}")


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def on_startup():
    from backend.shared.config.logging import logger
    from backend.shared.config.database import SessionLocal

    db = SessionLocal()
    try:
        # 1. Register this unified service with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="main_api",
                display_name="Migration Platform — Main API",
                base_url="http://localhost:8000",
                version="1.0.0",
                metadata={"unified": True, "replaces_ports": list(range(8003, 8018))},
            )
            # Update all service URLs to point to 8000
            from sqlalchemy import text
            db.execute(text("""
                UPDATE service_registry
                SET base_url = 'http://localhost:8000', updated_at = NOW()
                WHERE service_name IN (
                    'platform_kernel', 'workflow_engine', 'intelligence_service',
                    'intelligence_service_v2', 'simulation_engine', 'masking_service',
                    'plugin_service', 'extended_connectors', 'operations_console',
                    'scheduler_service', 'schema_mapping_service',
                    'enterprise_execution', 'enterprise_security',
                    'control_plane'
                )
            """))
            db.commit()
        except Exception as e:
            logger.warning("Service Registry update failed", error=str(e))

        # 2. Register all plugins with PluginManager
        try:
            from backend.plugins.validators.validator_plugins import register_all_validators
            from backend.plugins.transformers.transformer_plugins import register_all_transformers
            from backend.plugins.notifiers.notifier_plugins import register_all_notifiers
            from backend.plugins.policy.policy_plugins import register_all_policies
            register_all_validators()
            register_all_transformers()
            register_all_notifiers()
            register_all_policies()
        except Exception as e:
            logger.warning("Plugin registration failed", error=str(e))

        # 3. Register extended connectors
        try:
            from backend.connectors.file.file_connector import FileConnector
            from backend.connectors.object_storage.object_storage_connector import ObjectStorageConnector
            from backend.connectors.api.rest_api_connector import RestApiConnector
            from backend.connectors.streaming.kafka_connector import KafkaConnector
            from backend.connector_framework.registry.connector_registry import ConnectorRegistry
            ConnectorRegistry.register("file",           FileConnector)
            ConnectorRegistry.register("csv",            FileConnector)
            ConnectorRegistry.register("parquet",        FileConnector)
            ConnectorRegistry.register("object_storage", ObjectStorageConnector)
            ConnectorRegistry.register("s3",             ObjectStorageConnector)
            ConnectorRegistry.register("rest_api",       RestApiConnector)
            ConnectorRegistry.register("kafka",          KafkaConnector)
        except Exception as e:
            logger.warning("Extended connector registration failed", error=str(e))

        # 4. Register DataMaskingNode with Workflow Engine
        try:
            from backend.masking.nodes.data_masking_node import register_masking_node
            register_masking_node()
        except Exception as e:
            logger.warning("DataMaskingNode registration failed", error=str(e))

        # 5. Sync all plugins to persistent catalog
        try:
            from backend.kernel.plugin_manager.plugin_manager import PluginManager
            PluginManager.sync_to_catalog(db)
        except Exception as e:
            logger.warning("Plugin catalog sync failed", error=str(e))

        # 6. Start Service Registry health checker
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.start_health_checker(interval_seconds=60)
        except Exception as e:
            logger.warning("Health checker start failed", error=str(e))

        # 7. Start Scheduler Engine background loop
        try:
            from backend.scheduler.engine.scheduler_engine import SchedulerEngine
            _sched = SchedulerEngine()
            _sched.start()
            logger.info("Scheduler Engine started")
        except Exception as e:
            logger.warning("Scheduler Engine start failed", error=str(e))

        # 8. Subscribe Knowledge Base to job completion events
        try:
            from backend.kernel.event_bus.event_bus import EventBus

            def _on_job_completed(event):
                if event.get("event_type") == "job.completed":
                    job_id    = event.get("resource_id")
                    tenant_id = event.get("tenant_id", "local")
                    if job_id:
                        from backend.shared.config.database import SessionLocal as SL
                        from backend.knowledge_base.store.knowledge_base import KnowledgeBase
                        _db = SL()
                        try:
                            KnowledgeBase().record_migration_outcome(_db, job_id, tenant_id)
                        except Exception:
                            pass
                        finally:
                            _db.close()

            EventBus.subscribe(["job.completed"], _on_job_completed)
        except Exception as e:
            logger.warning("Knowledge Base event subscription failed", error=str(e))

        # 9. Subscribe Notification Manager to Event Bus
        try:
            from backend.plugins.notifiers.notifier_plugins import NotificationManager
            NotificationManager.start()
        except Exception as e:
            logger.warning("NotificationManager start failed", error=str(e))

        logger.info(
            "Migration Platform Kernel started",
            port=8000,
            mode="unified",
            note="CDC+LiveIntelligence on port 8006, Metrics on port 8001",
        )

    finally:
        db.close()


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
def health():
    from backend.shared.config.redis import redis_client
    redis_ok = False
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    maintenance = False
    try:
        maintenance = bool(redis_client.exists("migration:maintenance:active"))
    except Exception:
        pass

    return {
        "status":           "ok" if redis_ok else "degraded",
        "service":          "migration_platform_kernel",
        "port":             8000,
        "version":          "1.0.0",
        "mode":             "unified",
        "redis":            "ok" if redis_ok else "unavailable",
        "maintenance_mode": maintenance,
        "companion_services": {
            "cdc_connectors": "http://localhost:8006",
            "metrics":        "http://localhost:8001",
            "workers":        "standalone processes (no HTTP port)",
        },
        "docs": "http://localhost:8000/docs",
    }


@app.get("/", tags=["Health"])
def root():
    return {
        "name":    "Migration Platform Kernel",
        "version": "1.0.0",
        "docs":    "http://localhost:8000/docs",
        "health":  "http://localhost:8000/health",
    }
