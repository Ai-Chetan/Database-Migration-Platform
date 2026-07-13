"""
Scheduler, Reporting & Knowledge Base — FastAPI Application
File: migration/backend/scheduler/main.py

Part 11: Scheduler, Reporting, Knowledge Base. Runs on port 8017.

Three operational capabilities:

  Scheduler      → Cron-based scheduling of migrations, intelligence scans,
                   data quality checks, benchmarks, and reports.
                   Uses croniter for cron expression parsing with timezone support.
                   Integrates with the Approval Workflow — scheduled jobs can
                   require approval before execution.

  Report Generator → Generates 6 report types from completed migration data:
                   migration_summary, validation_report, performance_report,
                   audit_report, data_quality_report, compliance_report.
                   All stored as JSONB in migration_reports table.

  Knowledge Base  → Every completed migration stores structured knowledge:
                   outcomes, type mapping patterns, error fixes, performance patterns.
                   The AI Copilot (Part 14) queries this for data-driven recommendations.
                   Entries are rated by usefulness and ranked accordingly.

Start:
    cd migration/
    uvicorn backend.scheduler.main:app --host 0.0.0.0 --port 8017 --reload

Install dependencies:
    pip install croniter pytz

Docs: http://localhost:8017/docs

ALL ENDPOINTS:

── SCHEDULER ─────────────────────────────────────────────────────────────────
    POST   /scheduler/jobs                    Create a scheduled job
    GET    /scheduler/jobs                    List scheduled jobs
    GET    /scheduler/jobs/{id}               Get job detail
    PUT    /scheduler/jobs/{id}               Update job
    DELETE /scheduler/jobs/{id}               Delete job
    POST   /scheduler/jobs/{id}/trigger       Trigger now (bypass cron)
    GET    /scheduler/jobs/{id}/runs          Run history
    POST   /scheduler/cron/validate           Validate cron expression

── REPORTING ─────────────────────────────────────────────────────────────────
    POST   /reports/generate                  Generate a report
    GET    /reports/{id}                      Get a report
    GET    /reports/job/{job_id}              List reports for a job
    GET    /reports/types                     List report types

── KNOWLEDGE BASE ─────────────────────────────────────────────────────────────
    POST   /knowledge/record/{job_id}         Record migration outcome
    POST   /knowledge/record/error            Record error + resolution
    POST   /knowledge/record/type-mappings    Record type mapping patterns
    GET    /knowledge/search                  Search similar migrations
    GET    /knowledge/errors                  Find error fixes
    GET    /knowledge/performance             Get performance patterns
    GET    /knowledge/entries                 List all entries
    GET    /knowledge/entries/{id}            Get entry detail
    POST   /knowledge/entries/{id}/rate       Rate an entry
    GET    /knowledge/summary                 Knowledge base statistics
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.scheduler.routers import scheduler_reporting_kb

app = FastAPI(
    title="Migration Platform — Scheduler, Reporting & Knowledge Base",
    description=(
        "Part 11: Three operational capabilities. "
        "Scheduler: cron-based job scheduling with timezone support, approval gates, "
        "and Event Bus integration. "
        "Report Generator: 6 report types (summary, validation, performance, audit, "
        "data quality, compliance) stored as structured JSON. "
        "Knowledge Base: stores migration outcomes, type mapping patterns, "
        "error fixes, and performance patterns — queryable by the AI Copilot (Part 14) "
        "for data-driven recommendations."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scheduler_reporting_kb.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        # Register with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="scheduler_service",
                display_name="Scheduler, Reporting & Knowledge Base",
                base_url="http://localhost:8017",
                version="1.0.0",
                metadata={
                    "part": 11,
                    "components": ["scheduler", "report_generator", "knowledge_base"],
                    "report_types": [
                        "migration_summary", "validation_report", "performance_report",
                        "audit_report", "data_quality_report", "compliance_report",
                    ],
                    "knowledge_entry_types": [
                        "migration_outcome", "type_mapping_pattern",
                        "performance_pattern", "error_pattern",
                        "schema_pattern", "cdc_pattern",
                    ],
                },
            )
        except Exception as e:
            logger.warning("Service Registry registration failed", error=str(e))

        # Subscribe Knowledge Base to job completion events
        try:
            from backend.kernel.event_bus.event_bus import EventBus

            def _on_job_completed(event):
                """Auto-record benchmark when any job completes."""
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

            EventBus.subscribe(["job.completed"], _on_job_completed, run_in_background=True)
            logger.info("Knowledge Base subscribed to job.completed events")
        except Exception as e:
            logger.warning("Event Bus subscription failed", error=str(e))

        # Start the scheduler background loop
        try:
            from backend.scheduler.engine.scheduler_engine import SchedulerEngine
            _scheduler = SchedulerEngine()
            _scheduler.start()
            logger.info("Scheduler Engine background loop started")
        except Exception as e:
            logger.warning("Scheduler Engine start failed", error=str(e))

        logger.info("Scheduler, Reporting & Knowledge Base started", port=8017)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    # Check croniter availability
    croniter_ok = False
    try:
        from croniter import croniter
        croniter_ok = True
    except ImportError:
        pass

    return {
        "status":      "ok",
        "service":     "scheduler_service",
        "port":        8017,
        "version":     "1.0.0",
        "components": {
            "scheduler":       True,
            "report_generator": True,
            "knowledge_base":  True,
        },
        "dependencies": {
            "croniter": croniter_ok,
        },
        "note": "" if croniter_ok else "Install croniter: pip install croniter pytz",
    }
