"""
Simulation Router
File: migration/backend/simulation/routers/simulation.py

Endpoints:
    POST /simulate                      → run a single simulation
    POST /simulate/compare              → compare multiple scenarios side by side
    POST /simulate/worker-sweep         → auto-sweep 2→4→8→16→32 workers
    GET  /simulate/runs                 → list saved simulation runs
    GET  /simulate/runs/{id}            → get one saved simulation run
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.simulation.engine.simulation_engine import SimulationEngine

router = APIRouter(prefix="/simulate", tags=["Simulation Engine"])
engine = SimulationEngine()


# ── Request models ─────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    connection_id:          Optional[str] = None
    name:                   Optional[str] = None
    worker_count:           int = 4
    chunk_size_strategy:    str = "size_based"
    chunk_size_override:    Optional[int] = None
    source_engine:          str = "mysql"
    target_engine:          str = "mysql"
    network_bandwidth_mbps: Optional[float] = None
    table_names:            Optional[List[str]] = None
    manual_tables:          Optional[List[Dict[str, Any]]] = None
    tenant_id:              str = "local"


class ScenarioItem(BaseModel):
    name:                   str
    worker_count:           int
    chunk_size_strategy:    str = "size_based"
    chunk_size_override:    Optional[int] = None
    network_bandwidth_mbps: Optional[float] = None


class CompareRequest(BaseModel):
    connection_id:  str
    source_engine:  str = "mysql"
    target_engine:  str = "mysql"
    tenant_id:      str = "local"
    scenarios:      List[ScenarioItem]


class WorkerSweepRequest(BaseModel):
    connection_id:          str
    source_engine:          str = "mysql"
    target_engine:          str = "mysql"
    chunk_size_strategy:    str = "size_based"
    network_bandwidth_mbps: Optional[float] = None
    tenant_id:              str = "local"
    worker_counts:          Optional[List[int]] = None   # None = [2,4,8,16,32]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", summary="Run a migration simulation")
def run_simulation(req: SimulateRequest, db: Session = Depends(get_db)):
    """
    Projects what a migration will look like BEFORE touching production.

    Returns:
    {
      "worker_count":               8,
      "chunk_size_strategy":        "size_based",
      "estimated_duration_str":     "9h 23m",
      "estimated_duration_sec":     33780,
      "estimated_rows_per_sec":     112500,
      "estimated_mb_per_sec":       43.2,
      "estimated_cpu_source_pct":   64.0,
      "estimated_cpu_target_pct":   96.0,     ← target CPU saturated!
      "estimated_network_gb":       5865.0,
      "estimated_network_mbps":     139.2,
      "estimated_target_storage_gb": 6375.0,
      "failure_probability_pct":    12.0,
      "bottleneck":                 "target_write_cpu",
      "total_rows":                 4200000000,
      "total_size_gb":              5100.0,
      "table_breakdown":            [...top 20 tables by duration...],
      "recommendations":            ["Target DB CPU is projected at 96%..."],
      "data_source":                "metadata_catalog"
    }

    Data sources (priority order):
      1. manual_tables  — supply explicit row counts/sizes for each table
      2. connection_id  — loads real stats from Metadata Catalog (Part 3)
      3. No data        — returns empty result with instructions

    chunk_size_strategy options:
      size_based   — target 32MB/chunk (default, best for most cases)
      count_based  — fixed row count/chunk (good for wide-row tables)
      streaming    — very small chunks (lowest memory, highest overhead)
      uuid_sparse  — offset-based (for UUID/sparse PKs)
      full_table   — single chunk per table (only for tiny tables)

    network_bandwidth_mbps: supply if you know your network is constrained.
    Set to 100 for a 100Mbps link to see if network becomes the bottleneck.
    """
    if not req.connection_id and not req.manual_tables:
        raise HTTPException(
            status_code=400,
            detail="Either connection_id (to load from Metadata Catalog) or "
                   "manual_tables must be provided."
        )

    if req.worker_count < 1 or req.worker_count > 256:
        raise HTTPException(
            status_code=400,
            detail="worker_count must be between 1 and 256."
        )

    result = engine.simulate(
        db=db,
        connection_id=req.connection_id,
        worker_count=req.worker_count,
        chunk_size_strategy=req.chunk_size_strategy,
        chunk_size_override=req.chunk_size_override,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        network_bandwidth_mbps=req.network_bandwidth_mbps,
        table_names=req.table_names,
        manual_tables=req.manual_tables,
        tenant_id=req.tenant_id,
        name=req.name,
    )
    return result.to_dict()


@router.post("/compare", summary="Compare multiple migration scenarios side by side")
def compare_scenarios(req: CompareRequest, db: Session = Depends(get_db)):
    """
    Run multiple what-if scenarios and compare them.
    Results are sorted fastest-first.

    Example request:
    {
      "connection_id": "abc-123",
      "source_engine": "mysql",
      "target_engine": "postgresql",
      "scenarios": [
        {"name": "2 workers",   "worker_count": 2},
        {"name": "8 workers",   "worker_count": 8},
        {"name": "16 workers",  "worker_count": 16},
        {"name": "16 streaming","worker_count": 16, "chunk_size_strategy": "streaming"}
      ]
    }

    Response:
    {
      "scenarios":        4,
      "fastest_scenario": "16 workers",
      "fastest_duration": "2h 20m",
      "comparison": [
        {"scenario_name": "16 workers", "estimated_duration_str": "2h 20m", ...},
        {"scenario_name": "8 workers",  "estimated_duration_str": "4h 41m", ...},
        ...
      ]
    }
    """
    if not req.scenarios:
        raise HTTPException(status_code=400, detail="At least one scenario is required.")
    if len(req.scenarios) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 scenarios per comparison.")

    return engine.compare(
        db=db,
        connection_id=req.connection_id,
        scenarios=[s.dict() for s in req.scenarios],
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        tenant_id=req.tenant_id,
    )


@router.post("/worker-sweep", summary="Sweep worker counts to find optimal configuration")
def worker_sweep(req: WorkerSweepRequest, db: Session = Depends(get_db)):
    """
    Automatically simulates across a range of worker counts and returns
    a comparison showing ETA, CPU, and failure probability at each level.

    Default sweep: [2, 4, 8, 16, 32] workers.
    Or supply custom worker_counts: [1, 2, 4, 8, 12, 16, 24, 32].

    This directly answers: "How many workers should I use?"

    Response shows:
    - Duration curve (how much faster each additional worker makes it)
    - CPU curve (when do you start saturating the DB?)
    - Failure probability curve (when does more workers = more risk?)
    - Sweet spot recommendation (best duration/risk tradeoff)

    Example response snippet:
    {
      "sweet_spot_workers": 8,
      "sweet_spot_reason":  "Best duration/risk tradeoff: 4h 41m at 12% failure probability",
      "sweep": [
        {"workers": 2,  "duration": "9h 23m", "cpu_source": 16, "failure_pct": 5},
        {"workers": 4,  "duration": "4h 41m", "cpu_source": 32, "failure_pct": 8},
        {"workers": 8,  "duration": "2h 21m", "cpu_source": 64, "failure_pct": 12},
        {"workers": 16, "duration": "1h 11m", "cpu_source": 95, "failure_pct": 28},
        {"workers": 32, "duration": "0h 37m", "cpu_source": 95, "failure_pct": 45},
      ]
    }
    """
    counts = req.worker_counts or [2, 4, 8, 16, 32]
    counts = sorted(set(max(1, min(256, c)) for c in counts))

    scenarios = [
        {
            "name":                   f"{w} workers",
            "worker_count":           w,
            "chunk_size_strategy":    req.chunk_size_strategy,
            "network_bandwidth_mbps": req.network_bandwidth_mbps,
        }
        for w in counts
    ]

    comparison = engine.compare(
        db=db,
        connection_id=req.connection_id,
        scenarios=scenarios,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        tenant_id=req.tenant_id,
    )

    # Find sweet spot: best duration where failure_pct < 20% and cpu < 85%
    sweep = comparison.get("comparison", [])
    sweet_spot = None
    sweet_reason = ""

    for s in sweep:
        if (s.get("failure_probability_pct", 100) < 20 and
                s.get("estimated_cpu_source_pct", 100) < 85):
            sweet_spot = s.get("worker_count") or s.get("scenario_name", "").split()[0]
            sweet_reason = (
                f"Best duration/risk tradeoff: {s['estimated_duration_str']} "
                f"at {s['failure_probability_pct']}% failure probability, "
                f"{s['estimated_cpu_source_pct']}% source CPU."
            )
            break

    if not sweet_spot and sweep:
        # Fallback: just pick the fastest that doesn't saturate CPU
        for s in sorted(sweep, key=lambda x: x.get("estimated_duration_sec", 999999)):
            if s.get("estimated_cpu_source_pct", 100) < 85:
                sweet_spot = s.get("worker_count") or s.get("scenario_name", "").split()[0]
                sweet_reason = f"Fastest without CPU saturation: {s['estimated_duration_str']}"
                break

    return {
        "sweet_spot_workers": sweet_spot,
        "sweet_spot_reason":  sweet_reason,
        "worker_counts_tested": counts,
        "sweep": sweep,
    }


@router.get("/runs", summary="List saved simulation runs")
def list_runs(
    connection_id: Optional[str] = None,
    tenant_id:     str = "local",
    limit:         int = 20,
    db:            Session = Depends(get_db),
):
    """Returns saved simulation runs, most recent first."""
    conditions = ["tenant_id=:tid"]
    params: Dict[str, Any] = {"tid": tenant_id, "lim": limit}
    if connection_id:
        conditions.append("connection_id=:cid")
        params["cid"] = connection_id

    rows = db.execute(
        text(f"""
            SELECT id, name, worker_count, chunk_size_strategy,
                   estimated_duration_str, estimated_rows_per_sec,
                   failure_probability_pct, bottleneck,
                   total_rows, data_source, created_at
            FROM simulation_runs
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC LIMIT :lim
        """),
        params
    ).fetchall()

    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)

    return {"total": len(result), "runs": result}


@router.get("/runs/{run_id}", summary="Get one saved simulation run")
def get_run(run_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM simulation_runs WHERE id=:id"),
        {"id": run_id}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Simulation run {run_id} not found")

    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d
