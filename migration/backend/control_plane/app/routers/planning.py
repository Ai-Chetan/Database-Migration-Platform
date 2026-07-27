"""
Planning Router
File: migration/backend/control_plane/app/routers/planning.py

NEW FILE - the third cause of the startup warning:
    [WARN] Control Plane routers not loaded: cannot import name 'planning'
    from 'backend.control_plane.app.routers'

Wires together two existing, fully-built components that had no HTTP
layer connecting them:
    MigrationTableRepository   -> creates migration_tables rows for a job
    AdaptiveChunkPlanner       -> computes optimal chunk size/strategy per table

Note: AdaptiveChunkPlanner.compute_all_tables() UPDATEs migration_tables
rows (assumes they already exist) - it does not INSERT them. This router
creates the migration_tables rows FIRST via MigrationTableRepository,
then calls the planner to fill in computed_chunk_size/strategy for each.

Endpoints:
    POST /jobs/{job_id}/planning/tables   -> register tables for a job (creates migration_tables rows)
    POST /jobs/{job_id}/planning/compute  -> run adaptive chunk planning across all registered tables
    GET  /jobs/{job_id}/planning/tables   -> list tables + their computed plans for a job
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict, List

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.control_plane.app.repositories.migration_table_repository import MigrationTableRepository
from backend.control_plane.app.repositories.migration_job_repository import MigrationJobRepository
from backend.enterprise.adaptive_chunk_planner.planner import AdaptiveChunkPlanner
from backend.shared.config.logging import logger

router = APIRouter(prefix="/jobs/{job_id}/planning", tags=["Migration Planning"])

table_repo = MigrationTableRepository()
job_repo   = MigrationJobRepository()
planner    = AdaptiveChunkPlanner()


class RegisterTablesRequest(BaseModel):
    tables: List[str]
    primary_key_columns: Optional[Dict[str, str]] = None


class ComputePlanRequest(BaseModel):
    source_config:  Dict
    source_db_type: str = "mysql"
    target_db_type: str = "mysql"
    primary_key_columns: Optional[Dict[str, str]] = None


def _check_job_access(job, user: CurrentUser):
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not user.can("*") and job.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found.")


@router.post("/tables", summary="Register tables to migrate for this job")
def register_tables(
    job_id: str,
    req:    RegisterTablesRequest,
    user:   CurrentUser = Depends(require_permission("jobs:create")),
    db:     Session = Depends(get_db),
):
    """
    Creates migration_tables rows for each table name. Call this after
    schema discovery/mapping (Schema Mapping Service) has determined which
    tables will be migrated, and before POST /planning/compute.
    """
    job = job_repo.get_job_by_id(db, job_id)
    _check_job_access(job, user)

    pk_cols = req.primary_key_columns or {}
    created = []
    for table_name in req.tables:
        pk_col = pk_cols.get(table_name, "id")
        table = table_repo.create_table_entry(db, job_id, table_name, pk_col)
        created.append({
            "id":                 str(table.id),
            "table_name":         table.table_name,
            "primary_key_column": table.primary_key_column,
            "status":             table.status,
        })

    db.execute(
        text("UPDATE migration_jobs SET total_tables=:n WHERE id=:id"),
        {"n": len(req.tables), "id": job_id}
    )
    db.commit()

    logger.info("Tables registered for job", job_id=job_id, count=len(created))
    return {"job_id": job_id, "total": len(created), "tables": created}


@router.post("/compute", summary="Run adaptive chunk planning for all registered tables")
def compute_chunk_plans(
    job_id: str,
    req:    ComputePlanRequest,
    user:   CurrentUser = Depends(require_permission("jobs:create")),
    db:     Session = Depends(get_db),
):
    """
    Runs the Adaptive Chunk Planner across all tables already registered
    via POST /planning/tables. Computes optimal chunk_size and strategy
    per table based on row count, average row size, and PK distribution.
    """
    job = job_repo.get_job_by_id(db, job_id)
    _check_job_access(job, user)

    rows = db.execute(
        text("SELECT table_name FROM migration_tables WHERE job_id=:jid"),
        {"jid": job_id}
    ).fetchall()
    table_names = [r[0] for r in rows]

    if not table_names:
        raise HTTPException(
            status_code=400,
            detail="No tables registered for this job. Call POST /planning/tables first."
        )

    plans = planner.compute_all_tables(
        table_names=table_names,
        source_config=req.source_config,
        db=db,
        job_id=job_id,
        pk_columns=req.primary_key_columns,
        source_db_type=req.source_db_type,
        target_db_type=req.target_db_type,
    )

    result = {}
    total_chunks = 0
    for tname, plan in plans.items():
        result[tname] = {
            "row_count":              plan.row_count,
            "avg_row_size_bytes":     plan.avg_row_size_bytes,
            "pk_distribution":        plan.pk_distribution,
            "computed_chunk_size":    plan.computed_chunk_size,
            "computed_chunk_count":   plan.computed_chunk_count,
            "strategy_used":          plan.strategy_used,
            "estimated_duration_sec": plan.estimated_duration_sec,
            "memory_estimate_mb":     plan.memory_estimate_mb,
            "notes":                  plan.notes,
        }
        total_chunks += plan.computed_chunk_count

    db.execute(
        text("UPDATE migration_jobs SET total_chunks=:n WHERE id=:id"),
        {"n": total_chunks, "id": job_id}
    )
    db.commit()

    logger.info("Chunk planning computed", job_id=job_id, tables=len(result), total_chunks=total_chunks)
    return {"job_id": job_id, "total_chunks": total_chunks, "plans": result}


@router.get("/tables", summary="List registered tables and their plans")
def list_planning_tables(
    job_id: str,
    user:   CurrentUser = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    job = job_repo.get_job_by_id(db, job_id)
    _check_job_access(job, user)

    rows = db.execute(
        text("""
            SELECT id, table_name, primary_key_column, status,
                   total_rows, computed_chunk_size, avg_row_size_bytes
            FROM migration_tables WHERE job_id=:jid ORDER BY table_name
        """),
        {"jid": job_id}
    ).fetchall()

    tables = []
    for row in rows:
        d = dict(row._mapping)
        d["id"] = str(d["id"])
        tables.append(d)

    return {"job_id": job_id, "total": len(tables), "tables": tables}
