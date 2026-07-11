"""
Plugin Refactor Service — FastAPI Application
File: migration/backend/plugins/main.py

Part 8: Plugin Refactor. Runs on port 8013 (merged with masking service)
OR as a standalone service on port 8014.

Converts existing engines into proper kernel plugins:
  ValidatorPlugin      → row_count, checksum, sample, null_check, business_rule
  TransformerPlugin    → direct, rename, constant, expression, transform, lookup, mask, synthesize
  NotifierPlugin       → email, slack, teams, webhook, pagerduty
  PolicyPlugin         → forbidden_lossy_conversion, require_approval, max_downtime,
                          require_validation, max_chunk_size, require_backup, require_masking_for_pii

Every plugin registers with PluginManager (Part 1) at startup.
This makes them discoverable via GET /plugins/{type},
available to WorkflowEngine nodes, and extensible via Marketplace (Part 14).

Consolidation note: this service can be merged into the Control Plane
(port 8000) per the 6-service consolidation plan. All routers are
self-contained and can be included in any FastAPI app.

Start standalone:
    cd migration/
    uvicorn backend.plugins.main:app --host 0.0.0.0 --port 8014 --reload

Docs: http://localhost:8014/docs

ALL ENDPOINTS:

── PLUGIN DISCOVERY ──────────────────────────────────────────────────────────
    GET  /plugins/types                     List all plugin types
    GET  /plugins/{type}                    List plugins of one type
    GET  /plugins/validators/list           List available validators
    GET  /plugins/transformers/kinds        List mapping_kind values
    GET  /plugins/policies/list             List policy types

── VALIDATORS ────────────────────────────────────────────────────────────────
    POST /plugins/validators/run            Run a validator on a chunk

── NOTIFIERS ─────────────────────────────────────────────────────────────────
    POST /plugins/notifiers/configure       Configure + activate a notifier
    POST /plugins/notifiers/test            Test a notifier config

── POLICIES ──────────────────────────────────────────────────────────────────
    POST /plugins/policies/check/{job_id}   Run all policies for a job
    POST /plugins/policies/configure        Create/update a policy rule
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.plugins.routers import plugins

app = FastAPI(
    title="Migration Platform — Plugin Refactor Service",
    description=(
        "Part 8: Converts all existing engines into proper kernel plugins. "
        "Validators (row_count, checksum, sample, null_check, business_rule), "
        "Transformers (8 mapping_kinds including mask+synthesize from Part 7), "
        "Notifiers (Email, Slack, Teams, Webhook, PagerDuty), "
        "Policies (7 organizational governance rules). "
        "All register with PluginManager at startup for Marketplace extensibility."
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


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    # Register all plugins with PluginManager
    try:
        from backend.plugins.validators.validator_plugins import register_all_validators
        register_all_validators()
    except Exception as e:
        logger.warning("Validator registration failed", error=str(e))

    try:
        from backend.plugins.transformers.transformer_plugins import register_all_transformers
        register_all_transformers()
    except Exception as e:
        logger.warning("Transformer registration failed", error=str(e))

    try:
        from backend.plugins.notifiers.notifier_plugins import register_all_notifiers
        register_all_notifiers()
    except Exception as e:
        logger.warning("Notifier registration failed", error=str(e))

    try:
        from backend.plugins.policy.policy_plugins import register_all_policies
        register_all_policies()
    except Exception as e:
        logger.warning("Policy registration failed", error=str(e))

    # Start NotificationManager (subscribes to Event Bus)
    try:
        from backend.plugins.notifiers.notifier_plugins import NotificationManager
        NotificationManager.start()
        logger.info("NotificationManager started — subscribed to Event Bus")
    except Exception as e:
        logger.warning("NotificationManager start failed", error=str(e))

    # Sync all registrations to the persistent plugin catalog
    db = SessionLocal()
    try:
        try:
            from backend.kernel.plugin_manager.plugin_manager import PluginManager
            count = PluginManager.sync_to_catalog(db)
            logger.info("Plugin catalog synced", count=count)
        except Exception as e:
            logger.warning("Plugin catalog sync failed", error=str(e))

        # Register with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="plugin_service",
                display_name="Plugin Refactor Service",
                base_url="http://localhost:8014",
                version="1.0.0",
                metadata={
                    "part": 8,
                    "plugin_counts": {
                        "validators":   5,
                        "transformers": 8,
                        "notifiers":    5,
                        "policies":     7,
                    },
                },
            )
        except Exception as e:
            logger.warning("Service Registry registration failed", error=str(e))

        logger.info("Plugin Refactor Service started", port=8014)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager
        all_plugins = PluginManager.list_plugins()
        counts = {}
        for p in all_plugins:
            ptype = p["plugin_type"]
            counts[ptype] = counts.get(ptype, 0) + 1
    except Exception:
        counts = {}

    return {
        "status":       "ok",
        "service":      "plugin_service",
        "port":         8014,
        "version":      "1.0.0",
        "plugin_counts": counts,
    }
