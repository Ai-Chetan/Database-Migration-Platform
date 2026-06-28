"""
Rollback Engine Router
File: migration/backend/enterprise/routers/rollback.py

Endpoints:
    POST /jobs/{id}/rollback/generate    → generate rollback plan before migration
    GET  /jobs/{id}/rollback/plan        → get the rollback plan
    POST /jobs/{id}/rollback/dry-run     → preview what rollback would do
    POST /jobs/{id}/rollback/execute     → execute the rollback
    GET  /jobs/{id}/rollback/log         → step-by-step execution log
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict, List

from backend.shared.config.database import get_db
from backend.enterprise.rollback_engine.rollback_engine import RollbackEngine
from backend.enterprise.connection_manager.connection_manager import ConnectionManager

router  = APIRouter(prefix="/jobs", tags=["Rollback Engine"])
engine  = RollbackEngine()
cm      = ConnectionManager()


class GenerateRollbackRequest(BaseModel):
    migration_plan:  Dict              # from POST /projects/{id}/plan
    table_states:    Optional[Dict[str, str]] = None
    # {table_name: "empty"|"had_data"}
    # "empty" → TRUNCATE is safe
    # "had_data" → DELETE only migrated rows (preserve pre-existing data)


class ExecuteRollbackRequest(BaseModel):
    target_connection_id: Optional[str] = None
    target_config:        Optional[Dict] = None


@router.post("/{job_id}/rollback/generate", summary="Generate rollback plan")
def generate_rollback(
    job_id: str,
    req:    GenerateRollbackRequest,
    db:     Session = Depends(get_db)
):
    """
    Generates a rollback plan for the migration.

    CALL THIS BEFORE STARTING MIGRATION — not after it fails.

    The rollback plan captures the reverse execution steps so that if
    anything goes wrong at any point during migration, you can cleanly
    undo everything and restore the target DB to its pre-migration state.

    Rollback steps are in REVERSE table order (FK constraint order):
      Migration: country → customer → orders
      Rollback:  orders → customer → country

    table_states tells the engine what to do per table:
      "empty":    table was empty before migration → TRUNCATE (fast)
      "had_data": table had pre-existing rows → DELETE only our inserted rows

    The plan is saved to rollback_plans and linked to the migration job.
    """
    plan_id = engine.generate_plan(
        db=db,
        job_id=job_id,
        migration_plan=req.migration_plan,
        target_config={},   # Not needed at generation time
        table_states=req.table_states or {},
    )

    return {
        "plan_id": plan_id,
        "job_id":  job_id,
        "status":  "ready",
        "message": "Rollback plan generated. Call /rollback/dry-run to preview, "
                   "or /rollback/execute to run it if migration fails."
    }


@router.get("/{job_id}/rollback/plan", summary="Get the rollback plan")
def get_rollback_plan(job_id: str, db: Session = Depends(get_db)):
    """Returns the rollback plan linked to this job."""
    row = db.execute(
        text("SELECT rollback_plan_id FROM migration_jobs WHERE id = :jid"),
        {"jid": job_id}
    ).fetchone()

    if not row or not row[0]:
        raise HTTPException(
            status_code=404,
            detail=f"No rollback plan for job {job_id}. "
                   f"Run POST /jobs/{job_id}/rollback/generate first."
        )

    plan = engine.get_plan(db, str(row[0]))
    if not plan:
        raise HTTPException(status_code=404, detail="Rollback plan record not found")
    return plan


@router.post("/{job_id}/rollback/dry-run", summary="Preview rollback without executing")
def dry_run_rollback(
    job_id: str,
    req:    ExecuteRollbackRequest,
    db:     Session = Depends(get_db)
):
    """
    Shows exactly what the rollback would do WITHOUT executing anything.

    Returns a list of steps with the SQL that would be run:
    [
      {"step": 1, "table": "__all__", "type": "disable_fks",
       "would_execute": "SET FOREIGN_KEY_CHECKS = 0"},
      {"step": 2, "table": "orders",  "type": "truncate",
       "would_execute": "TRUNCATE TABLE `orders`"},
      ...
    ]

    Use this to review the rollback plan before committing to execution.
    """
    row = db.execute(
        text("SELECT rollback_plan_id FROM migration_jobs WHERE id = :jid"),
        {"jid": job_id}
    ).fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404,
                            detail="No rollback plan found. Run /rollback/generate first.")

    result = engine.execute(
        db=db,
        plan_id=str(row[0]),
        target_config=req.target_config or {},
        dry_run=True,
    )
    return result


@router.post("/{job_id}/rollback/execute", summary="Execute rollback — IRREVERSIBLE")
def execute_rollback(
    job_id: str,
    req:    ExecuteRollbackRequest,
    db:     Session = Depends(get_db)
):
    """
    ⚠ IRREVERSIBLE — Executes the rollback plan.

    This will:
      1. Disable FK constraint checks on target DB
      2. TRUNCATE or DELETE migrated data from each table (in reverse order)
      3. Re-enable FK constraints

    The migration job status is set to 'rolled_back'.

    Requires target_config or target_connection_id with write access to target DB.
    """
    row = db.execute(
        text("SELECT rollback_plan_id FROM migration_jobs WHERE id = :jid"),
        {"jid": job_id}
    ).fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404,
                            detail="No rollback plan found. Run /rollback/generate first.")

    # Resolve target config
    target_config = req.target_config
    if req.target_connection_id and not target_config:
        target_config = cm.get_config(db, req.target_connection_id)
        if not target_config:
            raise HTTPException(status_code=404, detail="Target connection not found")

    if not target_config:
        raise HTTPException(status_code=400,
                            detail="Provide target_connection_id or target_config")

    result = engine.execute(
        db=db,
        plan_id=str(row[0]),
        target_config=target_config,
        dry_run=False,
    )

    if result["success"]:
        # Mark job as rolled back
        db.execute(
            text("UPDATE migration_jobs SET status='rolled_back' WHERE id=:jid"),
            {"jid": job_id}
        )
        db.commit()

    return result


@router.get("/{job_id}/rollback/log", summary="Get rollback execution log")
def get_rollback_log(job_id: str, db: Session = Depends(get_db)):
    """Returns the step-by-step execution log for a completed rollback."""
    row = db.execute(
        text("SELECT rollback_plan_id FROM migration_jobs WHERE id = :jid"),
        {"jid": job_id}
    ).fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="No rollback plan for this job")

    plan_id = str(row[0])
    logs = db.execute(
        text("""
            SELECT step_number, step_type, table_name, status,
                   rows_affected, error_message, started_at, completed_at
            FROM rollback_execution_log
            WHERE rollback_plan_id = :pid
            ORDER BY step_number
        """),
        {"pid": plan_id}
    ).fetchall()

    entries = []
    for log in logs:
        d = dict(log._mapping)
        for k in ("started_at", "completed_at"):
            if hasattr(d.get(k), "isoformat"):
                d[k] = d[k].isoformat()
        entries.append(d)

    return {
        "job_id":  job_id,
        "plan_id": plan_id,
        "steps":   entries,
        "total":   len(entries),
        "passed":  sum(1 for e in entries if e.get("status") == "completed"),
        "failed":  sum(1 for e in entries if e.get("status") == "failed"),
    }
