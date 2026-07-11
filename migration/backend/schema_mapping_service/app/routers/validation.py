"""
Validation Router
File: migration/backend/schema_mapping_service/app/routers/validation.py

Endpoints:
    POST /projects/{id}/validate              → validate all tables in project
    POST /projects/{id}/validate/{table}      → validate one specific table
    GET  /projects/{id}/validation-results    → list all saved validation results
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from pydantic import BaseModel

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.validation_engine.validator import ValidationEngine
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository

router = APIRouter(prefix="/projects", tags=["Validation"])
repo   = MappingRepository()


class ValidateRequest(BaseModel):
    validations:    List[str] = ["row_count", "checksum", "null_check"]
    sample_size:    int = 100
    business_rules: Optional[List[dict]] = None
    source_config:  Optional[dict] = None
    target_config:  Optional[dict] = None


@router.post("/{project_id}/validate", summary="Validate all tables in a project")
def validate_project(project_id: str, req: ValidateRequest, db: Session = Depends(get_db)):
    """
    Runs validation for every table mapping in the project.

    Validation types:
      - row_count:     source count == target count
      - checksum:      MD5 aggregate of all column values matches
      - sample:        N random rows compared field-by-field
      - null_check:    no unexpected NULLs in target
      - business_rule: custom SQL rules (provide in business_rules list)

    Results are saved to schema_validation_results and returned.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    source_config = req.source_config or {}
    target_config = req.target_config or {}

    if not source_config or not target_config:
        raise HTTPException(
            status_code=400,
            detail="Provide source_config and target_config with DB credentials"
        )

    engine    = ValidationEngine(source_config, target_config)
    tbl_maps  = repo.list_table_mappings(db, project_id)

    all_results = []

    for tm in tbl_maps:
        src_tables = tm.get("source_tables", [])
        tgt_tables = tm.get("target_tables", [])
        col_maps   = repo.list_column_mappings(db, tm["id"])
        col_mapping = {cm["source_column"]: cm["target_column"] for cm in col_maps}

        if not src_tables or not tgt_tables:
            continue

        source_table = src_tables[0]
        target_table = tgt_tables[0]

        results = engine.validate_table(
            db=db,
            project_id=project_id,
            source_table=source_table,
            target_table=target_table,
            column_mapping=col_mapping,
            validations=req.validations,
            sample_size=req.sample_size,
            business_rules=req.business_rules,
        )

        for r in results:
            all_results.append({
                "validation_type": r.validation_type,
                "source_table":    r.source_table,
                "target_table":    r.target_table,
                "passed":          r.passed,
                "source_value":    r.source_value,
                "target_value":    r.target_value,
                "details":         r.details,
            })

    total  = len(all_results)
    passed = sum(1 for r in all_results if r["passed"])
    failed = total - passed

    return {
        "project_id": project_id,
        "total":      total,
        "passed":     passed,
        "failed":     failed,
        "overall":    "PASSED" if failed == 0 else "FAILED",
        "results":    all_results,
    }


@router.post("/{project_id}/validate/{table_name}", summary="Validate one specific table")
def validate_table(
    project_id: str,
    table_name: str,
    req: ValidateRequest,
    db: Session = Depends(get_db)
):
    """Validate a single source table for a project."""
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    if not req.source_config or not req.target_config:
        raise HTTPException(status_code=400, detail="Provide source_config and target_config")

    # Find the table mapping for this table
    tbl_maps    = repo.list_table_mappings(db, project_id)
    target_table = table_name
    col_mapping  = {}

    for tm in tbl_maps:
        if table_name in tm.get("source_tables", []):
            target_table = tm["target_tables"][0] if tm["target_tables"] else table_name
            col_maps     = repo.list_column_mappings(db, tm["id"])
            col_mapping  = {cm["source_column"]: cm["target_column"] for cm in col_maps}
            break

    engine  = ValidationEngine(req.source_config, req.target_config)
    results = engine.validate_table(
        db=db,
        project_id=project_id,
        source_table=table_name,
        target_table=target_table,
        column_mapping=col_mapping,
        validations=req.validations,
        sample_size=req.sample_size,
        business_rules=req.business_rules,
    )

    result_list = [
        {
            "validation_type": r.validation_type,
            "source_table":    r.source_table,
            "target_table":    r.target_table,
            "passed":          r.passed,
            "source_value":    r.source_value,
            "target_value":    r.target_value,
            "details":         r.details,
        }
        for r in results
    ]

    return {
        "table":    table_name,
        "passed":   sum(1 for r in result_list if r["passed"]),
        "failed":   sum(1 for r in result_list if not r["passed"]),
        "results":  result_list,
    }


@router.get("/{project_id}/validation-results", summary="Get saved validation results")
def get_validation_results(project_id: str, db: Session = Depends(get_db)):
    """Return all validation results previously saved for a project."""
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    rows = db.execute(
        text("""
            SELECT id, validation_type, source_table, target_table,
                   source_value, target_value, passed, details, created_at
            FROM schema_validation_results
            WHERE project_id = :pid
            ORDER BY created_at DESC
        """),
        {"pid": project_id}
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("id"), "hex"):
            d["id"] = str(d["id"])
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        results.append(d)

    return {
        "project_id": project_id,
        "total":      len(results),
        "passed":     sum(1 for r in results if r.get("passed")),
        "failed":     sum(1 for r in results if not r.get("passed")),
        "results":    results,
    }
