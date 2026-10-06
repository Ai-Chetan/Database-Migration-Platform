"""
Migration Platform Kernel — Unified Main Entry Point
File: migration/backend/main.py

CHANGES IN THIS VERSION (fixes applied):
  1. Removed the duplicate auth/users router registration that appeared
     twice at the bottom of the old file (lines 390-400 previously).
  2. Removed the import of backend.routers.auth / backend.routers.users
     entirely — that parallel auth system has been retired in favor of
     the mature, already-complete enterprise/security/rbac/auth.py +
     enterprise/routers/auth.py + TenantService system, which is what the
     frontend was actually built against and what your live database
     (users, tenants, roles, user_sessions tables) already matches.
  3. Added RateLimitMiddleware, which existed on disk but was never wired
     into the app. Now applied to /auth/login and password-reset endpoints.
  4. RBACMiddleware import removed — it depended on the retired
     shared/middleware/route_permissions.py and shared/auth/auth_service.py
     files (also retired). Route-level protection is now handled the way
     it always was in the OLD system: via Depends(require_permission(...))
     inside each router, using enterprise/security/rbac/auth.py.

Runs ALL platform services on port 8000.

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
    /auth, /tenants, /approvals              → Security + SaaS (enterprise/routers)
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

# ── Load .env before ANY other backend import ────────────────────────────────
# Several modules read secrets straight from os.environ at import time
# (shared/auth/auth_email.py reads SMTP_* as module-level globals; masking
# strategies and connection_manager.py read MIGRATION_ENCRYPTION_KEY). None
# of that works unless the real process environment has these values BEFORE
# those modules are imported - which is exactly why the logs showed
# "MIGRATION_ENCRYPTION_KEY not set — using ephemeral key" despite the key
# being present in a .env file: nothing had loaded it yet. This must be the
# very first backend-related statement in this file.
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(filename=".env", usecwd=True))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.shared.middleware.rate_limit import RateLimitMiddleware

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
    # FIX: "*" lets any website call the API with a stolen token. Set CORS_ORIGINS=http://localhost:5173,https://app.example.com
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting on sensitive auth endpoints (login, password reset).
# See shared/middleware/rate_limit.py for configuration via env vars
# (RATE_LIMIT_LOGIN_MAX, RATE_LIMIT_LOGIN_WINDOW, etc.)
app.add_middleware(RateLimitMiddleware)

# NOTE: RBACMiddleware has been removed. Permission enforcement is handled
# per-route via Depends(require_permission(...)) from
# backend.enterprise.security.rbac.auth — the same pattern already used
# throughout enterprise/routers/*.py. This matches how the OLD, retained
# auth system was designed from the start.


# ── Import and register all routers ───────────────────────────────────────────
# S02 FIX: routers used to be imported inside one big try/except per group, so a single
# broken import silently dropped a whole group of endpoints (only a print() on stdout).
# Now every router module is loaded on its own, failures are logged WITH traceback,
# recorded in ROUTER_STATUS (see GET /health/routers) and, when STRICT_ROUTERS=true
# (use it in tests/CI), startup fails instead of running half-broken.
#
# Order matters (first registered wins on duplicate paths): keep this list order.
# NOTE: monitoring_service's jobs list/detail overlap with control_plane's /jobs and are
# shadowed by it (harmless); its /jobs/{id}/tables|chunks|metrics and /metrics are additive.
import importlib
import logging as _logging
import traceback as _traceback

_router_log = _logging.getLogger("uvicorn.error")

ROUTER_MODULES = [
    # (group, module path)
    ("control_plane",   "backend.control_plane.app.routers.jobs"),
    ("control_plane",   "backend.control_plane.app.routers.planning"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.discovery"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.comparison"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.projects"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.mappings"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.validation"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.planning"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.constraints"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.recommendation"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.scripts"),
    ("schema_mapping",  "backend.schema_mapping_service.app.routers.versioning"),
    ("enterprise",      "backend.enterprise.routers.auth"),
    ("enterprise",      "backend.enterprise.routers.tenants"),
    ("enterprise",      "backend.enterprise.routers.approvals"),
    ("enterprise",      "backend.enterprise.routers.templates"),
    ("enterprise",      "backend.enterprise.routers.audit"),
    ("enterprise",      "backend.enterprise.routers.secrets"),
    ("enterprise",      "backend.enterprise.routers.connections"),
    ("enterprise",      "backend.enterprise.routers.dependency_graph"),
    ("enterprise",      "backend.enterprise.routers.rollback"),
    ("kernel",          "backend.kernel.routers.plugins"),
    ("kernel",          "backend.kernel.routers.events"),
    ("kernel",          "backend.kernel.routers.services"),
    ("kernel",          "backend.kernel.routers.catalog"),
    ("workflow_engine", "backend.workflow_engine.routers.workflows"),
    ("intelligence",    "backend.intelligence.routers.intelligence"),
    ("intelligence_service", "backend.intelligence_service.routers.intelligence_service"),
    ("simulation",      "backend.simulation.routers.simulation"),
    ("masking",         "backend.masking.routers.masking"),
    ("plugins",         "backend.plugins.routers.plugins"),
    ("extended_connectors", "backend.connectors.routers.extended_connectors"),
    ("operations",      "backend.operations.routers.operations"),
    ("scheduler_reporting_kb", "backend.scheduler.routers.scheduler_reporting_kb"),
    ("monitoring",      "backend.monitoring_service.app.routers.jobs"),
    ("monitoring",      "backend.monitoring_service.app.routers.workers"),
    ("monitoring",      "backend.monitoring_service.app.routers.chunks"),
    ("monitoring",      "backend.monitoring_service.app.routers.metrics"),
    ("connector_framework", "backend.connector_framework.routers.connectors"),
]

# module path -> {"group", "loaded", "error"}
ROUTER_STATUS = {}


def _load_routers():
    for group, mod_path in ROUTER_MODULES:
        status = {"group": group, "loaded": False, "error": None}
        try:
            module = importlib.import_module(mod_path)
            app.include_router(module.router)
            status["loaded"] = True
        except Exception as e:  # noqa: BLE001 - we want to catch everything and report it
            status["error"] = f"{type(e).__name__}: {e}"
            _router_log.error("Router %s FAILED to load: %s\n%s", mod_path, e, _traceback.format_exc())
        ROUTER_STATUS[mod_path] = status

    failed = [m for m, s in ROUTER_STATUS.items() if not s["loaded"]]
    if failed:
        msg = "Routers failed to load: " + ", ".join(failed)
        if os.environ.get("STRICT_ROUTERS", "false").lower() == "true":
            raise RuntimeError(msg)
        _router_log.error("%s  (set STRICT_ROUTERS=true to make this fatal)", msg)


_load_routers()


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


# ── Router load report ─────────────────────────────────────────────────────────

@app.get("/health/routers", tags=["Health"])
def health_routers():
    """Which router modules loaded and which failed (with the error text)."""
    failed = {m: s["error"] for m, s in ROUTER_STATUS.items() if not s["loaded"]}
    groups = {}
    for m, s in ROUTER_STATUS.items():
        g = groups.setdefault(s["group"], {"loaded": 0, "failed": 0})
        g["loaded" if s["loaded"] else "failed"] += 1
    return {
        "ok": not failed,
        "modules_total": len(ROUTER_STATUS),
        "modules_loaded": len(ROUTER_STATUS) - len(failed),
        "groups": groups,
        "failed": failed,
    }


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
        from backend.shared.config.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT is_active FROM maintenance_mode WHERE tenant_id IS NULL LIMIT 1")
            ).fetchone()
            maintenance = bool(row[0]) if row else False
        finally:
            db.close()
    except Exception:
        pass

    return {
        "status":           "ok" if redis_ok else "degraded",
        "service":          "migration_platform_kernel",
        "port":             8000,
        "version":          "1.0.0",
        "mode":             "unified",
        "router_failures": sum(1 for s in ROUTER_STATUS.values() if not s["loaded"]),
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
