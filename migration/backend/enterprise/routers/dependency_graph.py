"""
Dependency Graph Router
File: migration/backend/enterprise/routers/dependency_graph.py

Endpoints:
    POST /jobs/{id}/dependency-graph        → build FK dependency graph
    GET  /jobs/{id}/dependency-graph        → get saved graph
    GET  /jobs/{id}/dependency-graph/order  → get execution order as human-readable list
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List

from backend.shared.config.database import get_db
from backend.enterprise.dependency_graph.graph_executor import DependencyGraphExecutor

router   = APIRouter(prefix="/jobs", tags=["Dependency Graph"])
executor = DependencyGraphExecutor()


class BuildGraphRequest(BaseModel):
    source_schema: dict        # Full schema dict from SchemaDiscovery.discover()
    table_names:   Optional[List[str]] = None  # subset of tables, or None for all


@router.post("/{job_id}/dependency-graph", summary="Build FK dependency graph")
def build_graph(job_id: str, req: BuildGraphRequest, db: Session = Depends(get_db)):
    """
    Analyzes FK relationships in the source schema and builds a dependency
    graph that determines the correct table execution order.

    Example:
      country → state → city → customer → orders → order_items

    Result levels:
      Level 0: [country]           (no dependencies)
      Level 1: [state]             (depends on country)
      Level 2: [city]              (depends on state)
      Level 3: [customer]          (depends on city)
      Level 4: [orders]            (depends on customer)
      Level 5: [order_items]       (depends on orders)

    Tables at the same level can be migrated in PARALLEL.
    Tables at different levels must be migrated in order.

    If circular FK dependencies are detected, they are flagged and placed
    in the final level with a warning — they require manual handling.

    Saves the graph to table_dependency_graph and updates migration_tables
    with depth_level and execution_order.
    """
    graph = executor.build_graph(
        db=db,
        job_id=job_id,
        source_schema=req.source_schema,
        table_names=req.table_names,
    )

    return {
        "job_id":     job_id,
        "tables":     len(graph.nodes),
        "max_depth":  graph.max_depth,
        "has_cycles": graph.has_cycles,
        "cycles":     graph.cycle_info,
        "levels":     {
            str(depth): tables
            for depth, tables in graph.levels.items()
        },
        "execution_order": executor.get_execution_order(graph),
        "warning": (
            f"Circular FK dependencies detected in: {graph.cycle_info}. "
            "These tables will migrate last and may need manual FK handling."
            if graph.has_cycles else None
        )
    }


@router.get("/{job_id}/dependency-graph", summary="Get saved dependency graph")
def get_graph(job_id: str, db: Session = Depends(get_db)):
    """Returns the previously built dependency graph for a job."""
    rows = db.execute(
        text("""
            SELECT table_name, depends_on, depth_level, execution_order,
                   can_parallel, status
            FROM table_dependency_graph
            WHERE job_id = :jid
            ORDER BY execution_order
        """),
        {"jid": job_id}
    ).fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No dependency graph found for job {job_id}. "
                   f"Run POST /jobs/{job_id}/dependency-graph first."
        )

    import json
    nodes  = []
    levels: dict = {}
    for row in rows:
        deps = row.depends_on
        if isinstance(deps, str):
            try:
                deps = json.loads(deps)
            except Exception:
                deps = []
        entry = {
            "table_name":      row.table_name,
            "depends_on":      deps or [],
            "depth_level":     row.depth_level,
            "execution_order": row.execution_order,
            "can_parallel":    row.can_parallel,
            "status":          row.status,
        }
        nodes.append(entry)
        levels.setdefault(str(row.depth_level), []).append(row.table_name)

    return {
        "job_id": job_id,
        "total_tables": len(nodes),
        "max_depth":    max(n["depth_level"] for n in nodes) if nodes else 0,
        "levels":       levels,
        "nodes":        nodes,
    }


@router.get("/{job_id}/dependency-graph/order",
            summary="Get execution order as human-readable list")
def get_execution_order(job_id: str, db: Session = Depends(get_db)):
    """
    Returns the execution order in plain English.

    Example response:
    [
      {"level": 0, "tables": ["country"],    "parallel": false, "description": "Level 0: migrate country"},
      {"level": 1, "tables": ["state"],       "parallel": false, "description": "Level 1: migrate state"},
      {"level": 2, "tables": ["city"],        "parallel": false, "description": "Level 2: migrate city"},
      {"level": 3, "tables": ["customer","product"], "parallel": true,
       "description": "Level 3: migrate customer, product (in parallel)"}
    ]
    """
    rows = db.execute(
        text("""
            SELECT depth_level, array_agg(table_name ORDER BY table_name) AS tables
            FROM table_dependency_graph
            WHERE job_id = :jid
            GROUP BY depth_level
            ORDER BY depth_level
        """),
        {"jid": job_id}
    ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No graph found for job {job_id}")

    order = []
    for row in rows:
        tables = row.tables if isinstance(row.tables, list) else [row.tables]
        order.append({
            "level":       row.depth_level,
            "tables":      tables,
            "parallel":    len(tables) > 1,
            "description": f"Level {row.depth_level}: migrate {', '.join(tables)}" +
                           (" (in parallel)" if len(tables) > 1 else ""),
        })

    return {"job_id": job_id, "execution_order": order}
