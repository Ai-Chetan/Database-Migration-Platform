"""
Adaptive Chunk Planner Router
File: migration/backend/enterprise/routers/chunk_planning.py

Endpoints:
    POST /jobs/{id}/plan-chunks          → compute adaptive chunk plan for all tables
    GET  /jobs/{id}/chunk-plans          → get saved chunk plans
    POST /jobs/{id}/plan-chunks/{table}  → plan one specific table
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict

from backend.shared.config.database import get_db
from backend.enterprise.adaptive_chunk_planner.planner import AdaptiveChunkPlanner
from backend.enterprise.connection_manager.connection_manager import ConnectionManager

router  = APIRouter(prefix="/jobs", tags=["Adaptive Chunk Planning"])
planner = AdaptiveChunkPlanner()
cm      = ConnectionManager()


class ChunkPlanRequest(BaseModel):
    source_connection_id: Optional[str] = None
    source_config:        Optional[Dict] = None   # fallback if no connection_id
    source_db_type:       str = "mysql"
    target_db_type:       str = "mysql"


@router.post("/{job_id}/plan-chunks", summary="Compute adaptive chunk plans for all tables")
def plan_all_chunks(job_id: str, req: ChunkPlanRequest, db: Session = Depends(get_db)):
    """
    Analyzes every table in the job and computes the optimal chunk size.

    Replaces the old fixed chunk_size=100000 with intelligent per-table sizing.

    Strategy per table:
      - full_table:    < 1,000 rows → single chunk
      - size_based:    normal tables → target 32MB/chunk
      - count_based:   wide rows (>4KB avg) → cap at 5,000 rows/chunk
      - streaming:     > 500M rows → 10,000 rows + max parallelism
      - uuid_sparse:   UUID/sparse PKs → offset-based 10,000 rows/chunk
      - default:       fallback 100,000 rows if analysis fails

    Results are saved to adaptive_chunk_configs and migration_tables
    (updates computed_chunk_size, avg_row_size_bytes, total_rows).

    After calling this, use POST /jobs/{id}/plan-chunks/{table}/apply
    to regenerate chunks with the new sizes.
    """
    # Get source config
    if req.source_connection_id:
        source_config = cm.get_config(db, req.source_connection_id)
        if not source_config:
            raise HTTPException(status_code=404,
                                detail=f"Connection {req.source_connection_id} not found")
    elif req.source_config:
        source_config = req.source_config
    else:
        raise HTTPException(status_code=400,
                            detail="Provide source_connection_id or source_config")

    # Get all tables for this job
    tables = db.execute(
        text("SELECT table_name, primary_key_column FROM migration_tables WHERE job_id = :jid"),
        {"jid": job_id}
    ).fetchall()

    if not tables:
        raise HTTPException(status_code=404, detail=f"No tables found for job {job_id}")

    table_names = [row[0] for row in tables]
    pk_columns  = {row[0]: row[1] or "id" for row in tables}

    plans = planner.compute_all_tables(
        table_names=table_names,
        source_config=source_config,
        db=db,
        job_id=job_id,
        pk_columns=pk_columns,
        source_db_type=req.source_db_type,
        target_db_type=req.target_db_type,
    )

    return {
        "job_id":        job_id,
        "tables_planned": len(plans),
        "plans": [
            {
                "table_name":           name,
                "row_count":            p.row_count,
                "avg_row_size_bytes":   p.avg_row_size_bytes,
                "computed_chunk_size":  p.computed_chunk_size,
                "computed_chunk_count": p.computed_chunk_count,
                "strategy_used":        p.strategy_used,
                "pk_distribution":      p.pk_distribution,
                "estimated_duration_sec": p.estimated_duration_sec,
                "memory_estimate_mb":   p.memory_estimate_mb,
                "notes":                p.notes,
            }
            for name, p in plans.items()
        ]
    }


@router.post("/{job_id}/plan-chunks/{table_name}",
             summary="Compute adaptive chunk plan for one table")
def plan_single_table(
    job_id:     str,
    table_name: str,
    req:        ChunkPlanRequest,
    db:         Session = Depends(get_db)
):
    """Compute chunk plan for a single table."""
    if req.source_connection_id:
        source_config = cm.get_config(db, req.source_connection_id)
        if not source_config:
            raise HTTPException(status_code=404, detail="Connection not found")
    elif req.source_config:
        source_config = req.source_config
    else:
        raise HTTPException(status_code=400, detail="Provide source_connection_id or source_config")

    pk_row = db.execute(
        text("SELECT primary_key_column FROM migration_tables WHERE job_id=:jid AND table_name=:tname"),
        {"jid": job_id, "tname": table_name}
    ).fetchone()

    pk_col = pk_row[0] if pk_row and pk_row[0] else "id"
    plan   = planner.compute(
        table_name=table_name,
        source_config=source_config,
        db=db,
        job_id=job_id,
        pk_column=pk_col,
        source_db_type=req.source_db_type,
        target_db_type=req.target_db_type,
    )

    return {
        "table_name":           plan.table_name,
        "row_count":            plan.row_count,
        "avg_row_size_bytes":   plan.avg_row_size_bytes,
        "computed_chunk_size":  plan.computed_chunk_size,
        "computed_chunk_count": plan.computed_chunk_count,
        "strategy_used":        plan.strategy_used,
        "pk_distribution":      plan.pk_distribution,
        "pk_min":               plan.pk_min,
        "pk_max":               plan.pk_max,
        "estimated_duration_sec": plan.estimated_duration_sec,
        "memory_estimate_mb":   plan.memory_estimate_mb,
        "notes":                plan.notes,
    }


@router.get("/{job_id}/chunk-plans", summary="Get saved adaptive chunk plans")
def get_chunk_plans(job_id: str, db: Session = Depends(get_db)):
    """Returns all previously computed chunk plans for a job."""
    rows = db.execute(
        text("""
            SELECT table_name, row_count, avg_row_size_bytes,
                   computed_chunk_size, computed_chunk_count,
                   strategy_used, pk_distribution,
                   estimated_duration_sec, memory_estimate_mb, created_at
            FROM adaptive_chunk_configs
            WHERE job_id = :jid
            ORDER BY row_count DESC
        """),
        {"jid": job_id}
    ).fetchall()

    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"No chunk plans found for job {job_id}. Run POST /jobs/{job_id}/plan-chunks first.")

    plans = []
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        plans.append(d)

    total_rows   = sum(p.get("row_count", 0) or 0 for p in plans)
    total_chunks = sum(p.get("computed_chunk_count", 0) or 0 for p in plans)
    total_est    = sum(p.get("estimated_duration_sec", 0) or 0 for p in plans)

    return {
        "job_id":       job_id,
        "total_tables": len(plans),
        "total_rows":   total_rows,
        "total_chunks": total_chunks,
        "estimated_total_duration_sec": total_est,
        "plans":        plans,
    }
