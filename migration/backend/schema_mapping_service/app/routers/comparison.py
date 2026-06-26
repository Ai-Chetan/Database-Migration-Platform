"""
Comparison Router
File: migration/backend/schema_mapping_service/app/routers/comparison.py

Endpoints:
    POST /compare              → compare two schema versions
    POST /analyze-type         → analyze a single type conversion
    POST /analyze-table-types  → analyze all columns for a table mapping
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.comparison.schema_comparator import SchemaComparator
from backend.schema_mapping_service.app.datatype.type_engine import DataTypeEngine
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.schemas.schemas import (
    CompareRequest, TypeAnalysisRequest, TypeAnalysisResponse
)

router     = APIRouter(tags=["Schema Comparison"])
repo       = MappingRepository()
comparator = SchemaComparator()


@router.post("/compare", summary="Compare source and target schemas")
def compare_schemas(req: CompareRequest, db: Session = Depends(get_db)):
    """
    Compares two saved schema versions and returns a full structured diff.

    Detects:
      - Added/removed tables
      - Added/removed/changed/renamed columns (rename detection via Levenshtein)
      - PK and FK changes
      - Type conversion safety for each changed column
      - Overall risk level: low | medium | high

    Providing column_mappings tells the comparator about intentional renames
    so it doesn't flag them as drop+add:
    {
      "users": {"cust_name": "customer_name", "cust_email": "email"}
    }
    """
    src_version = repo.get_schema_version(db, req.source_schema_id)
    tgt_version = repo.get_schema_version(db, req.target_schema_id)

    if not src_version:
        raise HTTPException(status_code=404, detail=f"Source schema {req.source_schema_id} not found")
    if not tgt_version:
        raise HTTPException(status_code=404, detail=f"Target schema {req.target_schema_id} not found")

    diff = comparator.compare(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
        column_mappings=req.column_mappings or {},
    )

    return diff.to_dict()


@router.post("/analyze-type", summary="Analyze a single type conversion", response_model=TypeAnalysisResponse)
def analyze_type(req: TypeAnalysisRequest, db: Session = Depends(get_db)):
    """
    Analyze whether a specific type conversion is safe, lossy, unsafe, or conditional.

    Returns:
      - safety: safe | lossy | unsafe | conditional
      - action: proceed | warn | block | manual_review
      - cast_expression: the SQL expression to use in migration

    Example:
      source_type: "bigint", target_type: "int" → lossy, CAST(`col` AS SIGNED)
      source_type: "text",   target_type: "int" → unsafe, block
    """
    engine = DataTypeEngine(db=db)
    result = engine.analyze(
        source_col_ref=f"`{req.col_ref}`",
        source_type=req.source_type,
        target_type=req.target_type,
        source_db=req.source_db,
        target_db=req.target_db,
    )
    return TypeAnalysisResponse(
        source_type=result.source_type,
        target_type=result.target_type,
        safety=result.safety,
        requires_cast=result.requires_cast,
        cast_expression=result.cast_expression,
        notes=result.notes,
        action=result.action,
    )


@router.post("/analyze-table-types", summary="Analyze all column conversions for a table")
def analyze_table_types(
    source_schema_id: str,
    target_schema_id: str,
    source_table: str,
    target_table: str,
    column_mapping: dict,    # {src_col: tgt_col}
    source_db: str = "mysql",
    target_db: str = "mysql",
    db: Session = Depends(get_db)
):
    """
    Analyze type conversions for all mapped columns between two tables.
    Returns a dict of {target_col: conversion_result} for each mapped pair.
    """
    src_version = repo.get_schema_version(db, source_schema_id)
    tgt_version = repo.get_schema_version(db, target_schema_id)
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema version not found")

    src_cols = src_version["schema_data"]["tables"].get(source_table, {}).get("columns", {})
    tgt_cols = tgt_version["schema_data"]["tables"].get(target_table, {}).get("columns", {})

    if not src_cols:
        raise HTTPException(status_code=404, detail=f"Source table '{source_table}' not found in schema")
    if not tgt_cols:
        raise HTTPException(status_code=404, detail=f"Target table '{target_table}' not found in schema")

    engine  = DataTypeEngine(db=db)
    results = engine.analyze_table(
        source_cols=src_cols,
        target_cols=tgt_cols,
        col_mapping=column_mapping,
        source_table=source_table,
        source_db=source_db,
        target_db=target_db,
    )

    return {
        col: {
            "source_type":    r.source_type,
            "target_type":    r.target_type,
            "safety":         r.safety,
            "requires_cast":  r.requires_cast,
            "cast_expression": r.cast_expression,
            "action":         r.action,
            "notes":          r.notes,
        }
        for col, r in results.items()
    }
