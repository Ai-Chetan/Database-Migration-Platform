"""
Scheduler, Reporting, Knowledge Base Router
File: migration/backend/scheduler/routers/scheduler_reporting_kb.py

── SCHEDULER ─────────────────────────────────────────────────────────────────
    POST /scheduler/jobs                    Create a scheduled job
    GET  /scheduler/jobs                    List all scheduled jobs
    GET  /scheduler/jobs/{id}               Get scheduled job detail
    PUT  /scheduler/jobs/{id}               Update scheduled job
    DELETE /scheduler/jobs/{id}             Delete scheduled job
    POST /scheduler/jobs/{id}/trigger       Trigger a job right now
    GET  /scheduler/jobs/{id}/runs          List run history
    POST /scheduler/cron/validate           Validate a cron expression

── REPORTING ─────────────────────────────────────────────────────────────────
    POST /reports/generate                  Generate a report for a job
    GET  /reports/{id}                      Get a report
    GET  /reports/job/{job_id}              List reports for a job
    GET  /reports/types                     List available report types

── KNOWLEDGE BASE ─────────────────────────────────────────────────────────────
    POST /knowledge/record/{job_id}         Record migration outcome
    POST /knowledge/record/error            Record error + resolution
    POST /knowledge/record/type-mappings    Record type mapping patterns
    GET  /knowledge/search                  Search for similar migrations
    GET  /knowledge/errors                  Find error fixes
    GET  /knowledge/performance             Get performance patterns
    GET  /knowledge/entries                 List all entries
    GET  /knowledge/entries/{id}            Get entry detail
    POST /knowledge/entries/{id}/rate       Rate an entry (0.0-1.0)
    GET  /knowledge/summary                 Knowledge base statistics
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.scheduler.engine.scheduler_engine import SchedulerEngine
from backend.reporting.generators.report_generator import ReportGenerator
from backend.knowledge_base.store.knowledge_base import KnowledgeBase

router     = APIRouter(tags=["Scheduler / Reporting / Knowledge Base"])
scheduler  = SchedulerEngine()
reporter   = ReportGenerator()
kb         = KnowledgeBase()


# ── Request models ─────────────────────────────────────────────────────────────

class CreateScheduledJobRequest(BaseModel):
    name:             str
    job_type:         str
    cron_expression:  str
    job_config:       Dict[str, Any]
    description:      str = ""
    timezone:         str = "UTC"
    require_approval: bool = False
    tenant_id:        str = "local"


class UpdateScheduledJobRequest(BaseModel):
    name:             Optional[str] = None
    cron_expression:  Optional[str] = None
    job_config:       Optional[Dict[str, Any]] = None
    description:      Optional[str] = None
    timezone:         Optional[str] = None
    require_approval: Optional[bool] = None
    is_active:        Optional[bool] = None


class TriggerRequest(BaseModel):
    triggered_by: str = "manual"
    tenant_id:    str = "local"


class GenerateReportRequest(BaseModel):
    job_id:       str
    report_type:  str
    generated_by: str = "operator"
    tenant_id:    str = "local"


class RecordErrorRequest(BaseModel):
    error_message: str
    resolution:    str
    source_engine: str
    target_engine: str
    context:       Optional[Dict[str, Any]] = None
    job_id:        Optional[str] = None
    tenant_id:     str = "local"


class RecordTypeMappingsRequest(BaseModel):
    source_engine: str
    target_engine: str
    mappings:      List[Dict[str, Any]]
    job_id:        Optional[str] = None
    tenant_id:     str = "local"


class RateEntryRequest(BaseModel):
    rating: float   # 0.0 to 1.0


# ── Scheduler endpoints ────────────────────────────────────────────────────────

@router.post("/scheduler/jobs", summary="Create a scheduled job")
def create_scheduled_job(req: CreateScheduledJobRequest, db: Session = Depends(get_db)):
    """
    Create a cron-scheduled job.

    job_type options:
      intelligence_scan  → run metadata intelligence scan on a connection
      data_quality_scan  → run pre-migration data quality checks
      benchmark          → record benchmark for a completed migration job
      report             → generate a migration report
      migration          → start a migration (requires control plane integration)

    cron_expression (5-field standard cron):
      "0 2 * * SAT"     → Every Saturday at 2:00 AM
      "0 */6 * * *"     → Every 6 hours
      "0 1 * * *"       → Every day at 1:00 AM
      "*/30 * * * *"    → Every 30 minutes

    job_config examples:
      intelligence_scan: {"connection_id": "...", "source_config": {...}}
      data_quality_scan: {"connection_id": "...", "source_config": {...}, "schema_info": {...}}
      benchmark:         {"job_id": "..."}
      report:            {"job_id": "...", "report_type": "migration_summary"}
    """
    result = scheduler.create_scheduled_job(
        db=db,
        name=req.name,
        job_type=req.job_type,
        cron_expression=req.cron_expression,
        job_config=req.job_config,
        description=req.description,
        timezone=req.timezone,
        require_approval=req.require_approval,
        tenant_id=req.tenant_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/scheduler/jobs", summary="List all scheduled jobs")
def list_scheduled_jobs(tenant_id: str = "local", db: Session = Depends(get_db)):
    jobs = scheduler.list_scheduled_jobs(db, tenant_id)
    return {"total": len(jobs), "jobs": jobs}


@router.get("/scheduler/jobs/{job_id}", summary="Get scheduled job detail")
def get_scheduled_job(job_id: str, db: Session = Depends(get_db)):
    job = scheduler.get_scheduled_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Scheduled job {job_id} not found")
    return job


@router.put("/scheduler/jobs/{job_id}", summary="Update a scheduled job")
def update_scheduled_job(
    job_id: str, req: UpdateScheduledJobRequest, db: Session = Depends(get_db)
):
    updates = req.dict(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    return scheduler.update_scheduled_job(db, job_id, **updates)


@router.delete("/scheduler/jobs/{job_id}", summary="Delete a scheduled job")
def delete_scheduled_job(job_id: str, db: Session = Depends(get_db)):
    job = scheduler.get_scheduled_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Scheduled job {job_id} not found")
    return scheduler.delete_scheduled_job(db, job_id)


@router.post("/scheduler/jobs/{job_id}/trigger", summary="Trigger a scheduled job immediately")
def trigger_job(job_id: str, req: TriggerRequest, db: Session = Depends(get_db)):
    """
    Run a scheduled job right now, bypassing the cron schedule.
    Useful for testing configuration or running a one-time manual execution.
    """
    return scheduler.trigger_now(db, job_id, req.triggered_by, req.tenant_id)


@router.get("/scheduler/jobs/{job_id}/runs", summary="List run history for a scheduled job")
def list_runs(job_id: str, limit: int = 20, db: Session = Depends(get_db)):
    runs = scheduler.list_runs(db, job_id, limit)
    return {"scheduled_job_id": job_id, "total": len(runs), "runs": runs}


@router.post("/scheduler/cron/validate", summary="Validate a cron expression")
def validate_cron(cron_expression: str, timezone: str = "UTC"):
    """
    Validate a cron expression and return the next 5 scheduled run times.
    """
    try:
        from croniter import croniter
        import pytz
        tz  = pytz.timezone(timezone)
        import datetime as dt
        now = dt.datetime.now(tz)
        c   = croniter(cron_expression, now)
        next_runs = [str(c.get_next(dt.datetime)) for _ in range(5)]
        return {
            "valid":       True,
            "expression":  cron_expression,
            "timezone":    timezone,
            "next_5_runs": next_runs,
        }
    except ImportError:
        return {"valid": True, "expression": cron_expression,
                "note": "Install croniter for validation: pip install croniter pytz"}
    except Exception as e:
        return {"valid": False, "expression": cron_expression, "error": str(e)}


# ── Reporting endpoints ────────────────────────────────────────────────────────

@router.post("/reports/generate", summary="Generate a migration report")
def generate_report(req: GenerateReportRequest, db: Session = Depends(get_db)):
    """
    Generate a structured report for a migration job.

    report_type options:
      migration_summary    → overall outcome, duration, rows, validation status
      validation_report    → per-chunk validation results and checksums
      performance_report   → throughput, chunk timing, worker efficiency, benchmarks
      audit_report         → all operator actions and approvals for the job
      data_quality_report  → pre-migration data quality scan findings
      compliance_report    → audit trail for GDPR/HIPAA/SOC2 compliance

    Reports are stored in migration_reports table and retrievable by report_id.
    """
    result = reporter.generate(
        db=db,
        job_id=req.job_id,
        report_type=req.report_type,
        generated_by=req.generated_by,
        tenant_id=req.tenant_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/reports/{report_id}", summary="Get a generated report")
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = reporter.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


@router.get("/reports/job/{job_id}", summary="List reports for a job")
def list_job_reports(job_id: str, db: Session = Depends(get_db)):
    reports = reporter.list_reports(db, job_id)
    return {"job_id": job_id, "total": len(reports), "reports": reports}


@router.get("/reports/types", summary="List available report types")
def list_report_types():
    return {
        "report_types": [
            {"type": "migration_summary",   "description": "Overall job outcome, duration, rows, validation"},
            {"type": "validation_report",   "description": "Per-chunk validation results and checksums"},
            {"type": "performance_report",  "description": "Throughput, timing, worker efficiency, benchmarks"},
            {"type": "audit_report",        "description": "All operator actions and approvals"},
            {"type": "data_quality_report", "description": "Pre-migration data quality scan findings"},
            {"type": "compliance_report",   "description": "Audit trail for GDPR/HIPAA/SOC2 compliance"},
        ]
    }


# ── Knowledge Base endpoints ───────────────────────────────────────────────────

@router.post("/knowledge/record/{job_id}",
             summary="Record migration outcome in Knowledge Base")
def record_outcome(job_id: str, tenant_id: str = "local", db: Session = Depends(get_db)):
    """
    Called after a migration completes to store key facts for future reference.
    The AI Copilot (Part 14) queries this to make data-driven recommendations.

    Records: status, engine pair, worker count, chunk strategy, row count,
    duration, success rate, error patterns, and lessons learned.

    Call this automatically via the Scheduler (job_type=benchmark)
    or manually after each migration.
    """
    result = kb.record_migration_outcome(db, job_id, tenant_id)
    if not result:
        raise HTTPException(status_code=400,
                            detail=f"Job {job_id} not found or has no chunk data")
    return result


@router.post("/knowledge/record/error",
             summary="Record an error and its resolution")
def record_error(req: RecordErrorRequest, db: Session = Depends(get_db)):
    """
    Record an error pattern and how it was resolved.
    Future migrations hitting the same error can query this.
    """
    return kb.record_error_pattern(
        db=db,
        error_message=req.error_message,
        resolution=req.resolution,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        context=req.context,
        job_id=req.job_id,
        tenant_id=req.tenant_id,
    )


@router.post("/knowledge/record/type-mappings",
             summary="Record type mapping patterns")
def record_type_mappings(req: RecordTypeMappingsRequest, db: Session = Depends(get_db)):
    return kb.record_type_mapping_pattern(
        db=db,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        mappings=req.mappings,
        job_id=req.job_id,
        tenant_id=req.tenant_id,
    )


@router.get("/knowledge/search", summary="Search for similar migration experiences")
def search_knowledge(
    source_engine: str,
    target_engine: str,
    entry_type:    Optional[str] = None,
    tenant_id:     str = "local",
    limit:         int = 10,
    db:            Session = Depends(get_db),
):
    """
    Find knowledge base entries for a similar migration scenario.
    Returns entries ordered by usefulness score (most helpful first).
    """
    results = kb.find_similar(db, source_engine, target_engine,
                              entry_type, tenant_id, limit)
    return {
        "query": {"source_engine": source_engine, "target_engine": target_engine},
        "total": len(results),
        "results": results,
    }


@router.get("/knowledge/errors", summary="Find recorded error fixes")
def find_error_fixes(
    error_fragment: str,
    source_engine:  str,
    target_engine:  str,
    tenant_id:      str = "local",
    db:             Session = Depends(get_db),
):
    results = kb.get_error_fixes(db, error_fragment, source_engine, target_engine, tenant_id)
    return {"total": len(results), "fixes": results}


@router.get("/knowledge/performance", summary="Get performance patterns for an engine pair")
def get_performance_patterns(
    source_engine: str,
    target_engine: str,
    approx_rows:   Optional[int] = None,
    tenant_id:     str = "local",
    db:            Session = Depends(get_db),
):
    results = kb.get_performance_patterns(db, source_engine, target_engine,
                                          approx_rows, tenant_id)
    return {"total": len(results), "patterns": results}


@router.get("/knowledge/entries", summary="List all knowledge base entries")
def list_entries(
    tenant_id:  str = "local",
    entry_type: Optional[str] = None,
    limit:      int = 50,
    db:         Session = Depends(get_db),
):
    entries = kb.list_entries(db, tenant_id, entry_type, limit)
    return {"total": len(entries), "entries": entries}


@router.get("/knowledge/entries/{entry_id}", summary="Get a knowledge base entry")
def get_entry(entry_id: str, db: Session = Depends(get_db)):
    entry = kb.get_entry(db, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return entry


@router.post("/knowledge/entries/{entry_id}/rate", summary="Rate a knowledge base entry")
def rate_entry(entry_id: str, req: RateEntryRequest, db: Session = Depends(get_db)):
    """
    Rate an entry's usefulness (0.0 = not useful, 1.0 = very useful).
    Ratings are averaged into the usefulness_score, affecting search ranking.
    """
    if not 0.0 <= req.rating <= 1.0:
        raise HTTPException(status_code=400, detail="rating must be between 0.0 and 1.0")
    return kb.rate_entry(db, entry_id, req.rating)


@router.get("/knowledge/summary", summary="Knowledge base statistics")
def get_kb_summary(tenant_id: str = "local", db: Session = Depends(get_db)):
    """Returns aggregate statistics about the knowledge base."""
    return kb.get_summary(db, tenant_id)
