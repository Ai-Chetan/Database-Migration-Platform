"""
Mappings Router
File: migration/backend/schema_mapping_service/app/routers/mappings.py

Endpoints:
    POST /projects/{id}/table-mappings                → create table mapping
    GET  /projects/{id}/table-mappings                → list table mappings
    DELETE /projects/{id}/table-mappings/{mid}        → delete table mapping

    POST /table-mappings/{mid}/column-mappings        → add column mapping
    POST /table-mappings/{mid}/column-mappings/bulk   → bulk save
    GET  /table-mappings/{mid}/column-mappings        → list column mappings
    DELETE /column-mappings/{cid}                     → delete column mapping
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.datatype.type_engine import DataTypeEngine
from backend.schema_mapping_service.app.schemas.schemas import (
    CreateTableMappingRequest, CreateColumnMappingRequest, BulkColumnMappingRequest
)

router = APIRouter(tags=["Table & Column Mappings"])
repo   = MappingRepository()


# ── Table Mappings ────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/table-mappings", summary="Create table mapping")
def create_table_mapping(
    project_id: str,
    req: CreateTableMappingRequest,
    db: Session = Depends(get_db)
):
    """
    Create a mapping between source and target tables.

    mapping_type options:
      - single: one source table → one target table
      - split:  one source table → multiple target tables
      - merge:  multiple source tables → one target table
      - graph:  many-to-many (complex enterprise migrations)

    Example single:
      source_tables: ["users"],  target_tables: ["customers"]

    Example split:
      source_tables: ["customer"],
      target_tables: ["customer", "customer_address", "customer_contact"]

    Example merge:
      source_tables: ["users", "user_profile", "user_settings"],
      target_tables: ["customer_master"],
      join_condition: "LEFT JOIN user_profile ON users.id = user_profile.user_id ..."
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    return repo.save_table_mapping(
        db=db,
        project_id=project_id,
        mapping_type=req.mapping_type,
        source_tables=req.source_tables,
        target_tables=req.target_tables,
        join_condition=req.join_condition,
        notes=req.notes,
    )


@router.get("/projects/{project_id}/table-mappings", summary="List table mappings for project")
def list_table_mappings(project_id: str, db: Session = Depends(get_db)):
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return repo.list_table_mappings(db, project_id)


@router.delete("/projects/{project_id}/table-mappings/{mapping_id}", summary="Delete table mapping")
def delete_table_mapping(project_id: str, mapping_id: str, db: Session = Depends(get_db)):
    repo.delete_table_mapping(db, mapping_id)
    return {"deleted": mapping_id}


# ── Column Mappings ───────────────────────────────────────────────────────────

@router.post("/table-mappings/{table_mapping_id}/column-mappings", summary="Add column mapping")
def create_column_mapping(
    table_mapping_id: str,
    req: CreateColumnMappingRequest,
    db: Session = Depends(get_db)
):
    """
    Map one source column to one target column.

    mapping_kind options:
      - direct:     copy value as-is (same or compatible type)
      - rename:     copy from source_column → target_column (different names)
      - transform:  apply Python expression: {"expression": "row['val'] * 100"}
      - constant:   always write a fixed value: {"value": "India"}
      - expression: compute from multiple columns: {"expression": "row['first_name'] + ' ' + row['last_name']"}
      - lookup:     look up in another table: {"table": "country_master", "key_col": "code", "value_col": "id"}

    If conversion_safety is not provided, the type engine computes it automatically.
    """
    tm = repo.get_table_mapping(db, table_mapping_id)
    if not tm:
        raise HTTPException(status_code=404, detail=f"Table mapping {table_mapping_id} not found")

    # Auto-compute conversion safety if not provided
    safety      = req.conversion_safety
    requires_cast = req.requires_cast
    cast_expr   = req.cast_expression

    if not safety and req.source_type and req.target_type:
        engine  = DataTypeEngine(db=db)
        col_ref = f"`{req.source_table}`.`{req.source_column}`"
        result  = engine.analyze(col_ref, req.source_type, req.target_type)
        safety        = result.safety
        requires_cast = result.requires_cast
        cast_expr     = result.cast_expression

    return repo.save_column_mapping(
        db=db,
        table_mapping_id=table_mapping_id,
        source_table=req.source_table,
        source_column=req.source_column,
        source_type=req.source_type,
        target_table=req.target_table,
        target_column=req.target_column,
        target_type=req.target_type,
        mapping_kind=req.mapping_kind,
        mapping_config=req.mapping_config,
        conversion_safety=safety,
        requires_cast=requires_cast,
        cast_expression=cast_expr,
    )


@router.post("/table-mappings/{table_mapping_id}/column-mappings/bulk", summary="Bulk save column mappings")
def bulk_column_mappings(
    table_mapping_id: str,
    req: BulkColumnMappingRequest,
    db: Session = Depends(get_db)
):
    """
    Save multiple column mappings in one call.
    Used after accepting recommendations to bulk-create all accepted mappings.
    """
    tm = repo.get_table_mapping(db, table_mapping_id)
    if not tm:
        raise HTTPException(status_code=404, detail=f"Table mapping {table_mapping_id} not found")

    engine = DataTypeEngine(db=db)
    mappings = []
    for m in req.mappings:
        # Auto-compute safety
        safety, requires_cast, cast_expr = m.conversion_safety, m.requires_cast, m.cast_expression
        if not safety and m.source_type and m.target_type:
            col_ref = f"`{m.source_table}`.`{m.source_column}`"
            result  = engine.analyze(col_ref, m.source_type, m.target_type)
            safety, requires_cast, cast_expr = result.safety, result.requires_cast, result.cast_expression

        mappings.append({
            "source_table":     m.source_table,
            "source_column":    m.source_column,
            "source_type":      m.source_type,
            "target_table":     m.target_table,
            "target_column":    m.target_column,
            "target_type":      m.target_type,
            "mapping_kind":     m.mapping_kind,
            "mapping_config":   m.mapping_config,
            "conversion_safety": safety,
            "requires_cast":    requires_cast,
            "cast_expression":  cast_expr,
        })

    saved = repo.bulk_save_column_mappings(db, table_mapping_id, mappings)
    return {"saved": len(saved), "column_mappings": saved}


@router.get("/table-mappings/{table_mapping_id}/column-mappings", summary="List column mappings")
def list_column_mappings(table_mapping_id: str, db: Session = Depends(get_db)):
    return repo.list_column_mappings(db, table_mapping_id)


@router.delete("/column-mappings/{col_mapping_id}", summary="Delete column mapping")
def delete_column_mapping(col_mapping_id: str, db: Session = Depends(get_db)):
    repo.delete_column_mapping(db, col_mapping_id)
    return {"deleted": col_mapping_id}