"""
Metadata Catalog Router
File: migration/backend/kernel/routers/catalog.py

Endpoints:
    POST /catalog/write                       → write a catalog entry
    POST /catalog/write-bulk                  → bulk write entries for a connection
    GET  /catalog/{table_name}/{catalog_type} → get latest entry
    GET  /catalog/{table_name}                → get ALL catalog types for a table
    GET  /catalog/{table_name}/{catalog_type}/history → historical entries
    GET  /catalog/stale                       → list expired entries needing refresh
    DELETE /catalog/{table_name}              → delete all entries for a table
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from backend.shared.config.database import get_db
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog

router = APIRouter(prefix="/catalog", tags=["Metadata Catalog"])


class WriteEntryRequest(BaseModel):
    table_name:        str
    catalog_type:      str
    data:              Dict[str, Any]
    connection_id:     Optional[str] = None
    schema_version_id: Optional[str] = None
    tenant_id:         str = "local"
    ttl_hours:         Optional[int] = 24


class BulkEntry(BaseModel):
    table_name:   str
    catalog_type: str
    data:         Dict[str, Any]
    ttl_hours:    Optional[int] = 24


class WriteBulkRequest(BaseModel):
    connection_id: str
    entries:       List[BulkEntry]
    tenant_id:     str = "local"


@router.post("/write", summary="Write a metadata catalog entry")
def write_entry(req: WriteEntryRequest, db: Session = Depends(get_db)):
    """
    Write one catalog entry. This is the generic storage primitive that
    Part 3's Metadata Intelligence Layer (Statistics Collector, Relationship
    Mapper, Distribution Analyzer, LOB/Compression Detector) will call.

    catalog_type is a free-form string by design — Part 1 doesn't impose
    structure on what's "inside" each type, only how it's stored/retrieved.
    Recommended catalog_type values (Part 3 will use these):
      statistics, relationship, distribution, lob_detection,
      compression, hot_cold_classification, growth_rate

    Each write creates a NEW row — history is preserved naturally for
    trend analysis (e.g. growth_rate over time).
    """
    return MetadataCatalog.write(
        db=db,
        table_name=req.table_name,
        catalog_type=req.catalog_type,
        data=req.data,
        connection_id=req.connection_id,
        schema_version_id=req.schema_version_id,
        tenant_id=req.tenant_id,
        ttl_hours=req.ttl_hours,
    )


@router.post("/write-bulk", summary="Bulk write catalog entries for a connection")
def write_bulk(req: WriteBulkRequest, db: Session = Depends(get_db)):
    """
    Write many catalog entries in one call — used after scanning an entire
    schema (Part 3's collectors will scan every table and call this once).
    """
    count = MetadataCatalog.write_bulk(
        db=db,
        connection_id=req.connection_id,
        entries=[e.dict() for e in req.entries],
        tenant_id=req.tenant_id,
    )
    return {"written": count, "connection_id": req.connection_id}


@router.get("/{table_name}/{catalog_type}", summary="Get the latest catalog entry")
def get_entry(
    table_name:    str,
    catalog_type:  str,
    connection_id: Optional[str] = None,
    only_fresh:    bool = False,
    db:            Session = Depends(get_db),
):
    """
    Get the most recent catalog entry for a table+type combination.
    Set only_fresh=true to get None (404) if the entry has expired —
    useful for callers that want to trigger a recompute rather than use
    stale data (e.g. Cost Estimator before a quote).
    """
    result = MetadataCatalog.get(
        db=db, table_name=table_name, catalog_type=catalog_type,
        connection_id=connection_id, only_fresh=only_fresh,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {'fresh ' if only_fresh else ''}catalog entry found for "
                   f"table='{table_name}', catalog_type='{catalog_type}'"
        )
    return result


@router.get("/{table_name}", summary="Get all catalog types for a table")
def get_all_for_table(
    table_name:    str,
    connection_id: Optional[str] = None,
    db:            Session = Depends(get_db),
):
    """
    Get the latest entry of EVERY catalog_type for a table in one call —
    "tell me everything we know about this table." This is exactly the
    shape the Assessment Engine (Part 4) will consume to build its report.

    Response shape:
    {
      "statistics": {"data": {...}, "computed_at": "...", "is_fresh": true},
      "relationship": {"data": {...}, ...},
      "lob_detection": {"data": {...}, ...}
    }
    """
    result = MetadataCatalog.get_all_for_table(db, table_name, connection_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No catalog data found for table '{table_name}'. "
                   f"Run the Metadata Intelligence Layer scan first."
        )
    return {"table_name": table_name, "catalog": result}


@router.get("/{table_name}/{catalog_type}/history", summary="Get historical entries")
def get_history(
    table_name:    str,
    catalog_type:  str,
    connection_id: Optional[str] = None,
    limit:         int = 30,
    db:            Session = Depends(get_db),
):
    """
    Get historical snapshots for trend analysis — e.g. plotting growth_rate
    over the last 30 scans, or watching how row_count changed over time.
    """
    history = MetadataCatalog.get_history(
        db=db, table_name=table_name, catalog_type=catalog_type,
        connection_id=connection_id, limit=limit,
    )
    return {"table_name": table_name, "catalog_type": catalog_type,
            "count": len(history), "history": history}


@router.get("/stale/list", summary="List expired catalog entries needing refresh")
def list_stale(
    connection_id: Optional[str] = None,
    catalog_type:  Optional[str] = None,
    db:            Session = Depends(get_db),
):
    """
    Find catalog entries whose TTL has expired. Part 3's collectors (or,
    once built, Part 9's Scheduler) use this to know what needs recomputing
    rather than blindly re-scanning everything.
    """
    stale = MetadataCatalog.list_stale(db, connection_id=connection_id, catalog_type=catalog_type)
    return {"stale_count": len(stale), "entries": stale}


@router.delete("/{table_name}", summary="Delete all catalog entries for a table")
def delete_for_table(
    table_name:    str,
    connection_id: Optional[str] = None,
    db:            Session = Depends(get_db),
):
    """Used when a table is dropped or renamed in the source — clears stale references."""
    count = MetadataCatalog.delete_for_table(db, table_name, connection_id)
    return {"table_name": table_name, "deleted_entries": count}
