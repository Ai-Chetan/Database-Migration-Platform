"""
Data Masking & Synthetic Data Engine — FastAPI Application
File: migration/backend/masking/main.py

Part 7: Data Masking + Synthetic Data. Runs on port 8013.

Ensures production data never reaches non-production environments.

Three components:
  MaskingStrategies    → 7 deterministic masking strategies (hash, redact, partial,
                          encrypt, nullify, fixed_value, format_preserve)
  SyntheticGenerator   → 20 faker-based generators producing deterministic fake data
                          (same source row always becomes same fake row)
  DataMaskingNode      → WorkflowNode that slots between TransformNode and ValidateNode
                          in the migration pipeline

Start:
    cd migration/
    uvicorn backend.masking.main:app --host 0.0.0.0 --port 8013 --reload

Also required:
    pip install faker cryptography

Docs: http://localhost:8013/docs

ALL ENDPOINTS:

── RULE MANAGEMENT ───────────────────────────────────────────────────────────
    POST   /masking/rule-sets                        Create a masking rule set
    GET    /masking/rule-sets                        List rule sets
    GET    /masking/rule-sets/{id}                   Get rule set detail
    POST   /masking/rule-sets/{id}/rules             Add a rule to a set
    GET    /masking/rule-sets/{id}/rules             List rules in a set
    DELETE /masking/rule-sets/{id}/rules/{rule_id}   Delete a rule

── PREVIEW + TEST ────────────────────────────────────────────────────────────
    POST /masking/preview                            Preview on sample data
    POST /masking/test                               Test one rule on one value

── DISCOVERY ─────────────────────────────────────────────────────────────────
    GET  /masking/strategies                         List masking strategies
    GET  /masking/generators                         List synthetic generators

── LOGS ──────────────────────────────────────────────────────────────────────
    GET  /masking/logs/{job_id}                      Get masking activity for job

How to add masking to a migration:

  Option A — Per-column in schema_column_mappings (schema mapping service):
    Set mapping_kind='mask' or 'synthesize' on any column.
    The DataMaskingNode auto-detects these when it runs.

  Option B — Rule set applied at job level:
    1. POST /masking/rule-sets to create a rule set
    2. POST /masking/rule-sets/{id}/rules to add per-column rules
    3. Add DataMaskingNode to workflow definition with config:
       {"rule_set_id": "<your-rule-set-id>"}

  Both options produce identically masked output.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.masking.routers import masking

app = FastAPI(
    title="Migration Platform — Data Masking & Synthetic Data Engine",
    description=(
        "Part 7: Ensures production data never reaches non-production targets. "
        "Seven masking strategies (hash/redact/partial/encrypt/nullify/fixed/format-preserve) "
        "and 20 synthetic data generators, all deterministic for referential integrity. "
        "DataMaskingNode integrates directly into the Workflow Engine pipeline."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(masking.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        # Register DataMaskingNode with PluginManager + NODE_REGISTRY
        try:
            from backend.masking.nodes.data_masking_node import register_masking_node
            register_masking_node()
        except Exception as e:
            logger.warning("Could not register DataMaskingNode", error=str(e))

        # Register with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="masking_service",
                display_name="Data Masking & Synthetic Data Engine",
                base_url="http://localhost:8013",
                version="1.0.0",
                metadata={
                    "part": 7,
                    "strategies": ["hash", "redact", "partial", "encrypt",
                                   "nullify", "fixed_value", "format_preserve"],
                    "generators": 20,
                    "workflow_node": "DataMaskingNode",
                },
            )
        except Exception as e:
            logger.warning("Could not register with Service Registry", error=str(e))

        logger.info("Data Masking & Synthetic Data Engine started", port=8013)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":    "ok",
        "service":   "masking_service",
        "port":      8013,
        "version":   "1.0.0",
        "strategies": 7,
        "generators": 20,
        "workflow_node": "DataMaskingNode",
    }
