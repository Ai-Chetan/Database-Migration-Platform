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

CHANGES IN THIS VERSION (Stage 1 audit fix):
  Column-level code in the three engine files underneath this router
  (scheduler_engine.py, report_generator.py, knowledge_base.py) has
  already been checked and fixed separately (see those files' own
  docstrings for the real bugs found: migration_order -> execution_order,
  fabricated source_engine/target_engine/worker_count/chunk_strategy,
  migration_approvals/audit_logs column names).

  This router itself had the same architectural gaps found everywhere
  else in Phase B: zero authentication, and every tenant_id defaulting to
  the literal string "local" instead of the authenticated user's real
  tenant. Fixed by adding Depends(require_permission(...)) using the
  already-seeded "scheduler:*"/"reports:*"/"knowledge:*" permissions, and
  deriving tenant_id from the authenticated user everywhere.

  Also added tenant ownership checks that didn't exist before:
  scheduled_jobs, migration_reports, and knowledge_base entries could
  previously be read/modified/deleted by any authenticated user regardless
  of which tenant created them, by guessing/enumerating UUIDs. Knowledge
  Base entries are the one exception where cross-tenant access is
  legitimate BY DESIGN: the table has an is_public column specifically
  for sharing best-practice entries platform-wide, so the ownership check
  there allows tenant-match OR is_public=TRUE, matching the pattern
  get_summary() already used.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.scheduler.engine.scheduler_engine import SchedulerEngine
from backend.reporting.generators.report_generator import ReportGenerator
from backend.knowledge_base.store.knowledge_base import KnowledgeBase

router     = APIRouter(tags=["Scheduler / Reporting / Knowledge Base"])
scheduler  = SchedulerEngine()
reporter   = ReportGenerator()
kb         = KnowledgeBase()


# ── Request models (tenant_id removed - derived from the authenticated user) ──

class CreateScheduledJobRequest(BaseModel):
    name:             str
    job_type:         str
    cron_expression:  str
    job_config:       Dict[str, Any]
    description:      str = ""
    timezone:         str = "UTC"
    require_approval: bool = False


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


class GenerateReportRequest(BaseModel):
    job_id:       str
    report_type:  str


class RecordErrorRequest(BaseModel):
    error_message: str
    resolution:    str
    source_engine: str
    target_engine: str
    context:       Optional[Dict[str, Any]] = None
    job_id:        Optional[str] = None


class RecordTypeMappingsRequest(BaseModel):
    source_engine: str
    target_engine: str
    mappings:      List[Dict[str, Any]]
    job_id:        Optional[str] = None


class RateEntryRequest(BaseModel):
    rating: float   # 0.0 to 1.0


def _owned_scheduled_job(db: Session, job_id: str, user: CurrentUser) -> dict:
    job = scheduler.get_scheduled_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Scheduled job {job_id} not found")
    if not user.can("*") and job.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Scheduled job {job_id} not found")
    return job


# ── Scheduler endpoints ────────────────────────────────────────────────────────

@router.post("/scheduler/jobs", summary="Create a scheduled job")
def create_scheduled_job(
    req: CreateScheduledJobRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:write")),
):
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
        tenant_id=user.tenant_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/scheduler/jobs", summary="List scheduled jobs for your tenant")
def list_scheduled_jobs(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:read")),
):
    jobs = scheduler.list_scheduled_jobs(db, user.tenant_id)
    return {"total": len(jobs), "jobs": jobs}


@router.get("/scheduler/jobs/{job_id}", summary="Get scheduled job detail")
def get_scheduled_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:read")),
):
    return _owned_scheduled_job(db, job_id, user)


@router.put("/scheduler/jobs/{job_id}", summary="Update a scheduled job")
def update_scheduled_job(
    job_id: str, req: UpdateScheduledJobRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:write")),
):
    _owned_scheduled_job(db, job_id, user)
    updates = req.dict(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    return scheduler.update_scheduled_job(db, job_id, **updates)


@router.delete("/scheduler/jobs/{job_id}", summary="Delete a scheduled job")
def delete_scheduled_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:write")),
):
    _owned_scheduled_job(db, job_id, user)
    return scheduler.delete_scheduled_job(db, job_id)


@router.post("/scheduler/jobs/{job_id}/trigger", summary="Trigger a scheduled job immediately")
def trigger_job(
    job_id: str, req: TriggerRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:write")),
):
    """
    Run a scheduled job right now, bypassing the cron schedule.
    Useful for testing configuration or running a one-time manual execution.
    """
    _owned_scheduled_job(db, job_id, user)
    return scheduler.trigger_now(db, job_id, req.triggered_by, user.tenant_id)


@router.get("/scheduler/jobs/{job_id}/runs", summary="List run history for a scheduled job")
def list_runs(
    job_id: str, limit: int = 20,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("scheduler:read")),
):
    _owned_scheduled_job(db, job_id, user)
    runs = scheduler.list_runs(db, job_id, limit)
    return {"scheduled_job_id": job_id, "total": len(runs), "runs": runs}


@router.post("/scheduler/cron/validate", summary="Validate a cron expression")
def validate_cron(
    cron_expression: str, timezone: str = "UTC",
    user: CurrentUser = Depends(get_current_user),
):
    """Validate a cron expression and return the next 5 scheduled run times."""
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

def _job_tenant_or_404(db: Session, job_id: str, user: CurrentUser) -> None:
    row = db.execute(
        text("SELECT tenant_id FROM migration_jobs WHERE id=:id"), {"id": job_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if not user.can("*") and row[0] != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.post("/reports/generate", summary="Generate a migration report")
def generate_report(
    req: GenerateReportRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("reports:write")),
):
    """
    Generate a structured report for a migration job.

    report_type options:
      migration_summary    → overall outcome, duration, rows, validation status
      validation_report    → per-chunk validation results and checksums
      performance_report   → throughput, chunk timing, worker efficiency, benchmarks
      audit_report         → all operator actions and approvals for the job
      data_quality_report  → pre-migration data quality scan findings
      compliance_report    → audit trail for GDPR/HIPAA/SOC2 compliance
    """
    _job_tenant_or_404(db, req.job_id, user)
    result = reporter.generate(
        db=db,
        job_id=req.job_id,
        report_type=req.report_type,
        generated_by=user.email,
        tenant_id=user.tenant_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/reports/{report_id}", summary="Get a generated report")
def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("reports:read")),
):
    report = reporter.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    if not user.can("*") and report.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


@router.get("/reports/job/{job_id}", summary="List reports for a job")
def list_job_reports(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("reports:read")),
):
    _job_tenant_or_404(db, job_id, user)
    reports = reporter.list_reports(db, job_id)
    return {"job_id": job_id, "total": len(reports), "reports": reports}


@router.get("/reports/types", summary="List available report types")
def list_report_types(user: CurrentUser = Depends(get_current_user)):
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
def record_outcome(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    """
    Called after a migration completes to store key facts for future reference.
    Records: status, engine pair, worker count, row count, duration, success
    rate, error patterns, and lessons learned.
    """
    _job_tenant_or_404(db, job_id, user)
    result = kb.record_migration_outcome(db, job_id, user.tenant_id)
    if not result:
        raise HTTPException(status_code=400,
                            detail=f"Job {job_id} not found or has no chunk data")
    return result


@router.post("/knowledge/record/error",
             summary="Record an error and its resolution")
def record_error(
    req: RecordErrorRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    """Record an error pattern and how it was resolved."""
    return kb.record_error_pattern(
        db=db,
        error_message=req.error_message,
        resolution=req.resolution,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        context=req.context,
        job_id=req.job_id,
        tenant_id=user.tenant_id,
    )


@router.post("/knowledge/record/type-mappings",
             summary="Record type mapping patterns")
def record_type_mappings(
    req: RecordTypeMappingsRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    return kb.record_type_mapping_pattern(
        db=db,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        mappings=req.mappings,
        job_id=req.job_id,
        tenant_id=user.tenant_id,
    )


@router.get("/knowledge/search", summary="Search for similar migration experiences")
def search_knowledge(
    source_engine: str,
    target_engine: str,
    entry_type:    Optional[str] = None,
    limit:         int = 10,
    db:            Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """
    Find knowledge base entries for a similar migration scenario.
    Returns entries ordered by usefulness score (most helpful first).
    Includes your tenant's own entries plus any marked public.
    """
    results = kb.find_similar(db, source_engine, target_engine,
                              entry_type, user.tenant_id, limit)
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
    db:             Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    results = kb.get_error_fixes(db, error_fragment, source_engine, target_engine, user.tenant_id)
    return {"total": len(results), "fixes": results}


@router.get("/knowledge/performance", summary="Get performance patterns for an engine pair")
def get_performance_patterns(
    source_engine: str,
    target_engine: str,
    approx_rows:   Optional[int] = None,
    db:            Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    results = kb.get_performance_patterns(db, source_engine, target_engine,
                                          approx_rows, user.tenant_id)
    return {"total": len(results), "patterns": results}


@router.get("/knowledge/entries", summary="List knowledge base entries")
def list_entries(
    entry_type: Optional[str] = None,
    limit:      int = 50,
    db:         Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    entries = kb.list_entries(db, user.tenant_id, entry_type, limit)
    return {"total": len(entries), "entries": entries}


@router.get("/knowledge/entries/{entry_id}", summary="Get a knowledge base entry")
def get_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    entry = kb.get_entry(db, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    # Knowledge base entries can be intentionally shared platform-wide via
    # is_public - only enforce tenant match for private entries.
    if not entry.get("is_public") and not user.can("*") and entry.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return entry


@router.post("/knowledge/entries/{entry_id}/rate", summary="Rate a knowledge base entry")
def rate_entry(
    entry_id: str, req: RateEntryRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """
    Rate an entry's usefulness (0.0 = not useful, 1.0 = very useful).
    Ratings are averaged into the usefulness_score, affecting search ranking.
    Any user who can see an entry (own tenant or public) may rate it.
    """
    if not 0.0 <= req.rating <= 1.0:
        raise HTTPException(status_code=400, detail="rating must be between 0.0 and 1.0")
    entry = kb.get_entry(db, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    if not entry.get("is_public") and not user.can("*") and entry.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return kb.rate_entry(db, entry_id, req.rating)


@router.get("/knowledge/summary", summary="Knowledge base statistics")
def get_kb_summary(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("knowledge:read")),
):
    """Returns aggregate statistics about the knowledge base (your tenant + public entries)."""
    return kb.get_summary(db, user.tenant_id)
