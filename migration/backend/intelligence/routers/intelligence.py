"""
Intelligence Router
File: migration/backend/intelligence/routers/intelligence.py

Endpoints:
    POST /intelligence/scans                → start a full intelligence scan
    GET  /intelligence/scans/{id}           → get scan job status and progress
    GET  /intelligence/scans               → list all scans for a connection
    GET  /intelligence/tables/{table}       → get all catalog data for one table
    GET  /intelligence/tables/{table}/{type}→ get specific catalog type
    GET  /intelligence/summary             → schema-level summary (hot tables, LOB tables, skewed)
    GET  /intelligence/stale               → list tables needing re-scan
"""

import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.intelligence.analyzers.scan_orchestrator import ScanOrchestrator
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog

router       = APIRouter(prefix="/intelligence", tags=["Metadata Intelligence Layer"])
orchestrator = ScanOrchestrator()


class ScanRequest(BaseModel):
    connection_id: str
    source_config: Dict[str, Any]
    tenant_id:     str = "local"
    table_names:   Optional[List[str]] = None
    skip_types:    Optional[List[str]] = None
    background:    bool = True


@router.post("/scans", summary="Start a metadata intelligence scan")
def start_scan(req: ScanRequest, db: Session = Depends(get_db)):
    """
    Runs all four collectors against the source database:
      1. Statistics        → row counts, sizes, PK stats, growth rates
      2. Relationships     → FK cardinality (1:1, 1:N, high fan-out)
      3. Distributions     → column value distributions, skew, NULL rates
      4. LOB/Compression   → large object columns, compression detection

    All results are written to the Metadata Catalog (Part 1) and immediately
    available via GET /intelligence/tables/{table_name}.

    background=true (default):
      Returns {scan_job_id, status: "running"} immediately.
      Poll GET /intelligence/scans/{scan_job_id} for progress.

    background=false:
      Blocks until the scan completes. Fine for small schemas (<50 tables).
      Use background=true for large schemas.

    skip_types: skip specific collectors to save time, e.g.:
      ["distribution"] — skip column distribution analysis
      ["lob_detection", "compression"] — skip LOB/compression detection
    """
    result = orchestrator.run(
        db=db,
        connection_id=req.connection_id,
        source_config=req.source_config,
        tenant_id=req.tenant_id,
        table_names=req.table_names,
        skip_types=req.skip_types,
        background=req.background,
    )
    return result


@router.get("/scans/{scan_job_id}", summary="Get scan job status and progress")
def get_scan(scan_job_id: str, db: Session = Depends(get_db)):
    """
    Returns the current status of a scan job.

    Response:
    {
      "id":             "...",
      "status":         "running" | "completed" | "partial" | "failed",
      "tables_total":   150,
      "tables_scanned": 87,
      "tables_failed":  2,
      "started_at":     "...",
      "completed_at":   null,
      "table_results":  [last 20 completed tables with their status]
    }
    """
    row = db.execute(
        text("SELECT * FROM intelligence_scan_jobs WHERE id=:id"),
        {"id": scan_job_id}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Scan job {scan_job_id} not found")

    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()

    pct = 0
    if d.get("tables_total") and int(d["tables_total"]) > 0:
        pct = round(int(d.get("tables_scanned", 0)) / int(d["tables_total"]) * 100, 1)
    d["progress_pct"] = pct

    return d


@router.get("/scans", summary="List all scan jobs for a connection or tenant")
def list_scans(
    connection_id: Optional[str] = None,
    tenant_id:     str = "local",
    limit:         int = 20,
    db:            Session = Depends(get_db),
):
    conditions = ["tenant_id=:tid"]
    params: Dict[str, Any] = {"tid": tenant_id, "lim": limit}
    if connection_id:
        conditions.append("connection_id=:cid")
        params["cid"] = connection_id

    rows = db.execute(
        text(f"""
            SELECT id, status, tables_total, tables_scanned, tables_failed,
                   started_at, completed_at, created_at
            FROM intelligence_scan_jobs
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
    return {"scans": result, "total": len(result)}


@router.get("/tables/{table_name}", summary="Get all catalog data for one table")
def get_table_intelligence(
    table_name:    str,
    connection_id: Optional[str] = None,
    db:            Session = Depends(get_db),
):
    """
    Returns everything collected about a table — the "give me everything
    we know about this table" view used by the Assessment Engine (Part 4).

    Response:
    {
      "table_name": "orders",
      "catalog": {
        "statistics":    {"data": {...}, "computed_at": "...", "is_fresh": true},
        "growth_rate":   {"data": {...}, ...},
        "relationship":  {"data": {...}, ...},
        "distribution":  {"data": {...}, ...},
        "lob_detection": {"data": {...}, ...},
        "compression":   {"data": {...}, ...}
      }
    }
    """
    catalog = MetadataCatalog.get_all_for_table(db, table_name, connection_id)
    if not catalog:
        raise HTTPException(
            status_code=404,
            detail=f"No intelligence data found for table '{table_name}'. "
                   f"Run POST /intelligence/scans first."
        )
    return {"table_name": table_name, "catalog": catalog}


@router.get("/tables/{table_name}/{catalog_type}",
            summary="Get specific catalog type for a table")
def get_table_catalog_type(
    table_name:    str,
    catalog_type:  str,
    connection_id: Optional[str] = None,
    only_fresh:    bool = False,
    db:            Session = Depends(get_db),
):
    result = MetadataCatalog.get(
        db=db, table_name=table_name, catalog_type=catalog_type,
        connection_id=connection_id, only_fresh=only_fresh,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No '{catalog_type}' data for table '{table_name}'."
        )
    return {"table_name": table_name, "catalog_type": catalog_type, **result}


@router.get("/summary", summary="Schema-level intelligence summary")
def get_summary(
    connection_id: str,
    tenant_id:     str = "local",
    db:            Session = Depends(get_db),
):
    """
    Produces a schema-level summary by aggregating Metadata Catalog data.

    Returns:
    {
      "total_tables":         85,
      "total_rows":           12_400_000_000,
      "total_size_gb":        4700.3,
      "hot_tables":           ["orders", "events", "audit_log"],
      "lob_tables":           ["documents", "media_files"],
      "skewed_tables":        ["users"],   -- has highly skewed column distributions
      "high_growth_tables":   ["events"],  -- growing >10% per month
      "large_tables":         ["orders", "events"],  -- >100GB
      "top_10_by_size":       [...],
      "orphan_fk_tables":     []           -- tables with broken FK references
    }
    """
    # Aggregate from metadata_catalog
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (table_name)
                table_name, catalog_type, data, computed_at
            FROM metadata_catalog
            WHERE connection_id = :cid
              AND catalog_type IN ('statistics','lob_detection','distribution','growth_rate','relationship')
            ORDER BY table_name, computed_at DESC
        """),
        {"cid": connection_id}
    ).fetchall()

    import json as _json

    # Build per-table summary
    tables_data: Dict[str, Dict] = {}
    for row in rows:
        tname = row.table_name
        ctype = row.catalog_type
        data  = row.data
        if isinstance(data, str):
            try:
                data = _json.loads(data)
            except Exception:
                data = {}
        tables_data.setdefault(tname, {})[ctype] = data

    # Compute summary metrics
    total_rows   = 0
    total_size_gb = 0.0
    hot_tables   = []
    lob_tables   = []
    skewed_tables = []
    high_growth  = []
    large_tables = []
    orphan_tables = []

    table_sizes  = []

    for tname, data in tables_data.items():
        stats = data.get("statistics", {})
        rc    = stats.get("row_count", 0) or 0
        sg    = stats.get("size_gb", 0) or 0
        total_rows    += rc
        total_size_gb += sg
        table_sizes.append({"table": tname, "size_gb": sg, "rows": rc})

        if sg >= 100:
            large_tables.append(tname)

        lob = data.get("lob_detection", {})
        if lob.get("has_lob"):
            lob_tables.append(tname)

        dist = data.get("distribution", {})
        if dist.get("skewed_columns"):
            skewed_tables.append(tname)

        gr = data.get("growth_rate", {})
        rpm = gr.get("rows_per_month", 0) or 0
        if rc > 0 and rpm > 0:
            growth_pct = rpm / rc * 100
            if growth_pct > 10:
                high_growth.append({"table": tname, "growth_pct_per_month": round(growth_pct, 2)})

        rel = data.get("relationship", {})
        if isinstance(rel, dict):
            if rel.get("orphan_count", 0) > 0:
                orphan_tables.append(tname)

    top10 = sorted(table_sizes, key=lambda x: x["size_gb"], reverse=True)[:10]

    return {
        "connection_id":      connection_id,
        "total_tables":       len(tables_data),
        "total_rows":         total_rows,
        "total_size_gb":      round(total_size_gb, 2),
        "lob_tables":         lob_tables,
        "skewed_tables":      skewed_tables,
        "large_tables":       large_tables,        # > 100 GB
        "high_growth_tables": high_growth,
        "orphan_fk_tables":   orphan_tables,
        "top_10_by_size":     top10,
    }


@router.get("/stale", summary="List tables needing re-scan")
def get_stale(
    connection_id: Optional[str] = None,
    catalog_type:  Optional[str] = None,
    db:            Session = Depends(get_db),
):
    """
    Returns tables whose catalog entries have expired (TTL exceeded).
    The Scheduler (Part 9) will call this to trigger automatic re-scans.
    """
    stale = MetadataCatalog.list_stale(db, connection_id=connection_id,
                                        catalog_type=catalog_type)
    return {"stale_count": len(stale), "tables": stale}
