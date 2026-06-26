"""
Script Generation Router
File: migration/backend/schema_mapping_service/app/routers/scripts.py

Endpoints:
    POST /projects/{id}/scripts/generate   → generate Python/SQL/Airflow script
    GET  /projects/{id}/scripts            → list generated scripts
    GET  /scripts/{id}/download            → get script content (for download)
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.migration_generator.script_generator import (
    generate_python_script, generate_sql_script, generate_airflow_dag
)
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.schemas.schemas import GenerateScriptRequest

router = APIRouter(tags=["Script Generation"])
repo   = MappingRepository()


@router.post("/projects/{project_id}/scripts/generate", summary="Generate migration script")
def generate_script(
    project_id: str,
    req: GenerateScriptRequest,
    db: Session = Depends(get_db)
):
    """
    Generate a migration script for a specific table.

    script_type options:
      - python:       Full Python script with streaming reads and bulk inserts
      - sql:          SQL INSERT ... SELECT script with CAST placeholders
      - airflow_dag:  Apache Airflow DAG skeleton for all tables in the project

    Scripts for tables with unsafe conversions include ⚠ markers
    and TODO comments so developers know exactly what to fix.

    Scripts are saved to the DB and can be downloaded via GET /scripts/{id}/download.

    source_config and target_config are optional — if not provided,
    the service pulls them from the schema versions linked to the project.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    src_schema = src_version["schema_data"]
    tgt_schema = tgt_version["schema_data"]

    # Use provided configs or derive from schema versions
    source_config = req.source_config or {
        "engine":   src_version.get("db_type", "mysql"),
        "database": src_version.get("name", "source_db"),
    }
    target_config = req.target_config or {
        "engine":   tgt_version.get("db_type", "mysql"),
        "database": tgt_version.get("name", "target_db"),
    }

    # Get table mappings and column mappings for this table
    tbl_maps = repo.list_table_mappings(db, project_id)

    # Find the table mapping for the requested table
    target_table  = req.table_name
    col_mapping   = {}
    join_condition = ""
    mapping_type  = "single"

    for tm in tbl_maps:
        if req.table_name in tm.get("source_tables", []):
            target_table   = tm["target_tables"][0] if tm["target_tables"] else req.table_name
            mapping_type   = tm.get("mapping_type", "single")
            join_condition = tm.get("join_condition", "")
            col_maps       = repo.list_column_mappings(db, tm["id"])
            col_mapping    = {cm["source_column"]: cm["target_column"] for cm in col_maps}
            break

    if not col_mapping:
        # Fall back to identity mapping (same col names)
        src_cols    = src_schema.get("tables", {}).get(req.table_name, {}).get("columns", {})
        col_mapping = {c: c for c in src_cols}

    # Find unsafe conversions for this table
    dry_run = project.get("dry_run_result") or {}
    unsafe_cols = [
        u for u in dry_run.get("unsafe_conversions", [])
        if u.get("table") == req.table_name
    ]

    # ── Airflow DAG ────────────────────────────────────────────────────────────
    if req.script_type == "airflow_dag":
        all_tables = list(src_schema.get("tables", {}).keys())
        content    = generate_airflow_dag(
            project_name=project.get("name", project_id),
            tables=all_tables,
            source_config=source_config,
            target_config=target_config,
        )
        filename = f"{project_id}_airflow_dag.py"
        saved    = repo.save_script(
            db=db, project_id=project_id,
            script_type="airflow_dag", target_table="all",
            content=content, filename=filename
        )
        return {**saved, "content_preview": content[:500] + "..."}

    # ── Python script ──────────────────────────────────────────────────────────
    elif req.script_type == "python":
        content  = generate_python_script(
            source_table=req.table_name,
            target_table=target_table,
            column_mappings=col_mapping,
            source_config=source_config,
            target_config=target_config,
            mapping_type=mapping_type,
            join_condition=join_condition,
            unsafe_cols=unsafe_cols,
        )
        filename = f"migrate_{req.table_name}_to_{target_table}.py"

    # ── SQL script ─────────────────────────────────────────────────────────────
    elif req.script_type == "sql":
        content  = generate_sql_script(
            source_table=req.table_name,
            target_table=target_table,
            column_mappings=col_mapping,
            source_db=source_config.get("database", "source_db"),
            target_db=target_config.get("database", "target_db"),
            mapping_type=mapping_type,
            join_condition=join_condition,
            unsafe_cols=unsafe_cols,
        )
        filename = f"migrate_{req.table_name}_to_{target_table}.sql"

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown script_type '{req.script_type}'. Use: python | sql | airflow_dag"
        )

    saved = repo.save_script(
        db=db, project_id=project_id,
        script_type=req.script_type, target_table=target_table,
        content=content, filename=filename
    )
    return {**saved, "content_preview": content[:500] + "..."}


@router.get("/projects/{project_id}/scripts", summary="List generated scripts for a project")
def list_scripts(project_id: str, db: Session = Depends(get_db)):
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return repo.list_scripts(db, project_id)


@router.get("/scripts/{script_id}/download", response_class=PlainTextResponse,
            summary="Download script content as plain text")
def download_script(script_id: str, db: Session = Depends(get_db)):
    """
    Returns the full script content as plain text.
    Use this to download and save the generated .py or .sql file.
    """
    content = repo.get_script_content(db, script_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Script {script_id} not found")
    return content
