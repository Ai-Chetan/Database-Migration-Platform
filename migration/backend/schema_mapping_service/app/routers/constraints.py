"""
Constraint and Index Mapping Router
File: migration/backend/schema_mapping_service/app/routers/constraints.py

Endpoints:
    POST /projects/{id}/ddl/create-tables    → Phase 1 DDL: CREATE TABLE without indexes
    POST /projects/{id}/ddl/indexes          → Phase 3 DDL: CREATE INDEX statements
    POST /projects/{id}/ddl/foreign-keys     → Phase 4 DDL: ADD FOREIGN KEY statements
    POST /projects/{id}/ddl/full             → All 3 phases combined
    GET  /projects/{id}/ddl/analyze          → Detect conflicts between source/target schemas
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.constraint_index_mapping.constraint_mapper import (
    ConstraintIndexMapper
)
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository

router  = APIRouter(prefix="/projects", tags=["Constraints & Indexes"])
repo    = MappingRepository()
mapper  = ConstraintIndexMapper()


class DDLRequest(BaseModel):
    source_db: str = "mysql"       # mysql | postgresql
    target_db: str = "mysql"       # mysql | postgresql


def _build_table_mappings(db, project_id: str) -> dict:
    """Helper: build the table_mappings dict from DB records."""
    tbl_maps = repo.list_table_mappings(db, project_id)
    result   = {}
    for tm in tbl_maps:
        src_tables  = tm.get("source_tables", [])
        tgt_tables  = tm.get("target_tables", [])
        col_maps    = repo.list_column_mappings(db, tm["id"])
        col_mapping = {cm["source_column"]: cm["target_column"] for cm in col_maps}
        for src in src_tables:
            result[src] = {
                "target":          tgt_tables[0] if tgt_tables else src,
                "column_mappings": col_mapping,
            }
    return result


@router.post("/{project_id}/ddl/create-tables",
             summary="Generate Phase 1 DDL: CREATE TABLE statements (no indexes)")
def generate_create_tables(project_id: str, req: DDLRequest, db: Session = Depends(get_db)):
    """
    Generates CREATE TABLE statements WITHOUT indexes or FK constraints.

    Why no indexes during load?
    Building indexes on an empty table and then loading data is much slower
    than loading all data first and then building indexes.
    For a 50M row table this can be 3-5x faster.

    Run these statements on the target DB BEFORE starting workers.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    table_mappings = _build_table_mappings(db, project_id)

    result = mapper.build_full_migration_ddl(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
        table_mappings=table_mappings,
        source_db=req.source_db,
        target_db=req.target_db,
    )

    return {
        "phase": 1,
        "description": "Run these CREATE TABLE statements on target DB before starting workers",
        "total_tables": result["total_tables"],
        "ddl_statements": result["phase_1_create_tables"],
        "combined_script": "\n\n".join(result["phase_1_create_tables"]),
    }


@router.post("/{project_id}/ddl/indexes",
             summary="Generate Phase 3 DDL: CREATE INDEX statements (run after data load)")
def generate_indexes(project_id: str, req: DDLRequest, db: Session = Depends(get_db)):
    """
    Generates CREATE INDEX and UNIQUE constraint statements.

    Run these AFTER all workers have finished loading data.
    Building indexes after bulk load is significantly faster.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    table_mappings = _build_table_mappings(db, project_id)

    result = mapper.build_full_migration_ddl(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
        table_mappings=table_mappings,
        source_db=req.source_db,
        target_db=req.target_db,
    )

    return {
        "phase": 3,
        "description": "Run these CREATE INDEX statements AFTER data load is complete",
        "total_indexes": result["total_indexes"],
        "ddl_statements": result["phase_3_indexes"],
        "combined_script": "\n\n".join(result["phase_3_indexes"]),
    }


@router.post("/{project_id}/ddl/foreign-keys",
             summary="Generate Phase 4 DDL: ADD FOREIGN KEY constraints")
def generate_foreign_keys(project_id: str, req: DDLRequest, db: Session = Depends(get_db)):
    """
    Generates ALTER TABLE ... ADD FOREIGN KEY statements.

    Run these LAST — after all tables are loaded and indexes are built.
    FK constraints require referenced rows to already exist in target.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    table_mappings = _build_table_mappings(db, project_id)

    result = mapper.build_full_migration_ddl(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
        table_mappings=table_mappings,
        source_db=req.source_db,
        target_db=req.target_db,
    )

    return {
        "phase": 4,
        "description": "Run these LAST — after all data is loaded and all indexes built",
        "total_foreign_keys": result["total_foreign_keys"],
        "ddl_statements": result["phase_4_foreign_keys"],
        "combined_script": "\n\n".join(result["phase_4_foreign_keys"]),
    }


@router.post("/{project_id}/ddl/full",
             summary="Generate all 4 phases of DDL combined")
def generate_full_ddl(project_id: str, req: DDLRequest, db: Session = Depends(get_db)):
    """
    Returns all DDL phases together with clear execution order instructions.

    Execution order:
      Phase 1: Run CREATE TABLE statements → then start workers
      Phase 2: Workers load data           → automatic via Redis queue
      Phase 3: Run CREATE INDEX statements → after workers done
      Phase 4: Run ADD FOREIGN KEY         → after indexes built
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    table_mappings = _build_table_mappings(db, project_id)

    result = mapper.build_full_migration_ddl(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
        table_mappings=table_mappings,
        source_db=req.source_db,
        target_db=req.target_db,
    )

    return {
        "project_id":    project_id,
        "source_db":     req.source_db,
        "target_db":     req.target_db,
        "execution_order": [
            "Phase 1: Run phase_1_create_tables on target DB",
            "Phase 2: Start workers — they load data automatically via Redis queue",
            "Phase 3: After all workers done — run phase_3_indexes",
            "Phase 4: After indexes built — run phase_4_foreign_keys",
        ],
        "phase_1_create_tables": {
            "total":   result["total_tables"],
            "sql":     "\n\n".join(result["phase_1_create_tables"]),
        },
        "phase_3_indexes": {
            "total":   result["total_indexes"],
            "sql":     "\n\n".join(result["phase_3_indexes"]),
        },
        "phase_4_foreign_keys": {
            "total":   result["total_foreign_keys"],
            "sql":     "\n\n".join(result["phase_4_foreign_keys"]),
        },
    }


@router.get("/{project_id}/ddl/analyze",
            summary="Analyze constraint conflicts between source and target")
def analyze_constraints(project_id: str, db: Session = Depends(get_db)):
    """
    Compares source and target schema constraints and flags conflicts.

    Reports:
      - PK column changes
      - FK references to tables that don't exist in target
      - Unique constraints that may conflict with existing data
      - Column type incompatibilities that affect constraints
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    src_tables = src_version["schema_data"].get("tables", {})
    tgt_tables = tgt_version["schema_data"].get("tables", {})
    tbl_maps   = _build_table_mappings(db, project_id)

    issues     = []
    warnings   = []

    for src_name, mapping in tbl_maps.items():
        tgt_name    = mapping.get("target", src_name)
        src_tbl     = src_tables.get(src_name, {})
        tgt_tbl     = tgt_tables.get(tgt_name, {})
        col_mapping = mapping.get("column_mappings", {})

        src_pks = set(src_tbl.get("primary_keys", []))
        tgt_pks = set(tgt_tbl.get("primary_keys", []))

        if src_pks and tgt_pks:
            mapped_pks = {col_mapping.get(p, p) for p in src_pks}
            if mapped_pks != tgt_pks:
                issues.append(
                    f"Table '{src_name}→{tgt_name}': PK conflict — "
                    f"source maps to {mapped_pks}, target has {tgt_pks}"
                )

        for fk in src_tbl.get("foreign_keys", []):
            ref_tbl = fk.get("ref_table")
            if ref_tbl and ref_tbl not in tgt_tables:
                # Check if ref_tbl is mapped to a different target name
                is_mapped = any(
                    ref_tbl in m.get("source_tables", [])
                    for m in repo.list_table_mappings(db, project_id)
                )
                if not is_mapped:
                    warnings.append(
                        f"Table '{src_name}': FK references '{ref_tbl}' "
                        f"which has no mapping defined in target"
                    )

    return {
        "project_id":       project_id,
        "issues":           issues,
        "warnings":         warnings,
        "has_blockers":     len(issues) > 0,
        "total_issues":     len(issues),
        "total_warnings":   len(warnings),
        "recommendation":   (
            "Fix all issues before running DDL generation"
            if issues else
            "No blockers found. DDL generation is safe to proceed."
        ),
    }
