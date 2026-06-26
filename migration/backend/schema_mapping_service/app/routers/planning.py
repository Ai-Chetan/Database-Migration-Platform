"""
Dry Run & Plan Router
File: migration/backend/schema_mapping_service/app/routers/planning.py

Endpoints:
    POST /projects/{id}/dry-run       → analyze risk before migration
    POST /projects/{id}/plan          → generate ordered execution plan
    GET  /projects/{id}/plan          → get saved plan
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.migration_generator.plan_generator import MigrationPlanGenerator
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository

router    = APIRouter(prefix="/projects", tags=["Dry Run & Planning"])
repo      = MappingRepository()
generator = MigrationPlanGenerator()


def _build_table_mappings_dict(db, project_id: str, src_schema: dict, tgt_schema: dict) -> dict:
    """
    Build the table_mappings dict that plan_generator expects:
    {
      "users": {
        "target": "customers",
        "column_mappings": {"id": "id", "first_name": "fname", ...}
      }
    }
    """
    tbl_maps   = repo.list_table_mappings(db, project_id)
    result     = {}

    for tm in tbl_maps:
        src_tables = tm.get("source_tables", [])
        tgt_tables = tm.get("target_tables", [])
        col_maps   = repo.list_column_mappings(db, tm["id"])

        # Build col mapping dict: {src_col: tgt_col}
        col_dict = {}
        for cm in col_maps:
            col_dict[cm["source_column"]] = cm["target_column"]

        for src_tbl in src_tables:
            result[src_tbl] = {
                "target":          tgt_tables[0] if tgt_tables else src_tbl,
                "mapping_type":    tm.get("mapping_type", "single"),
                "target_tables":   tgt_tables,
                "join_condition":  tm.get("join_condition"),
                "column_mappings": col_dict,
                # alias used by plan_generator
                "new_table_name_schema": tgt_tables[0] if tgt_tables else src_tbl,
            }

    return result


@router.post("/{project_id}/dry-run", summary="Analyze migration risk before executing")
def dry_run(project_id: str, db: Session = Depends(get_db)):
    """
    Runs a full risk analysis without moving any data.

    Returns:
      - complexity:         LOW | MEDIUM | HIGH
      - tables_automatable: tables that can migrate automatically
      - tables_manual_only: tables with unsafe conversions needing manual scripts
      - total_rows:         estimated rows to migrate
      - estimated_duration: human-readable time estimate
      - unsafe_conversions: list of columns with unsafe type changes
      - lossy_conversions:  list of columns with lossy type changes
      - missing_mappings:   source tables with no mapping defined
      - risk_factors:       human-readable risk list
      - recommendations:    actionable advice

    Result is also saved to mapping_projects.dry_run_result for later retrieval.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found for this project")

    src_schema = src_version["schema_data"]
    tgt_schema = tgt_version["schema_data"]

    table_mappings = _build_table_mappings_dict(db, project_id, src_schema, tgt_schema)

    result = generator.dry_run(
        project_id=project_id,
        source_schema=src_schema,
        target_schema=tgt_schema,
        table_mappings=table_mappings,
    )

    result_dict = result.to_dict()
    repo.save_dry_run_result(db, project_id, result_dict)

    return result_dict


@router.post("/{project_id}/plan", summary="Generate ordered migration execution plan")
def generate_plan(project_id: str, db: Session = Depends(get_db)):
    """
    Generates a step-by-step execution plan for the migration.

    Steps are ordered to respect FK dependencies (referenced tables migrated first).
    Tables with unsafe conversions are separated into a manual step.

    Plan structure:
      Step 1: Create target tables (no indexes)
      Step 2: Migrate independent tables (no FK)
      Step 3: Migrate FK-dependent tables (in dependency order)
      Step 4: Rebuild indexes
      Step 5: Validate
      Step 6: Generate manual scripts (only if unsafe conversions exist)

    Requires dry-run to have been run first.
    Plan is saved to mapping_projects.migration_plan.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    # Run dry-run first if not already done
    dry_run_result = project.get("dry_run_result")
    if not dry_run_result:
        raise HTTPException(
            status_code=400,
            detail="Run /dry-run first before generating a plan"
        )

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    src_schema  = src_version["schema_data"]
    tgt_schema  = tgt_version["schema_data"]

    table_mappings = _build_table_mappings_dict(db, project_id, src_schema, tgt_schema)

    # Rebuild DryRunResult from saved dict
    from backend.schema_mapping_service.app.migration_generator.plan_generator import DryRunResult
    dr = DryRunResult(
        project_id=dry_run_result["project_id"],
        complexity=dry_run_result["complexity"],
        tables_total=dry_run_result["tables_total"],
        tables_automatable=dry_run_result["tables_automatable"],
        tables_manual_only=dry_run_result.get("tables_manual_only", 0),
        total_rows=dry_run_result["total_rows"],
        estimated_duration=dry_run_result["estimated_duration"],
        unsafe_conversions=dry_run_result["unsafe_conversions"],
        lossy_conversions=dry_run_result["lossy_conversions"],
        missing_mappings=dry_run_result["missing_mappings"],
        risk_factors=dry_run_result["risk_factors"],
        recommendations=dry_run_result["recommendations"],
        generated_at=dry_run_result["generated_at"],
    )
    # Attach the manual table list directly
    dr.tables_manual_only = [c["table"] for c in dry_run_result["unsafe_conversions"]]

    plan = generator.generate_plan(
        project_id=project_id,
        source_schema=src_schema,
        table_mappings=table_mappings,
        dry_run=dr,
    )

    plan_dict = plan.to_dict()
    repo.save_migration_plan(db, project_id, plan_dict)

    return plan_dict


@router.get("/{project_id}/plan", summary="Get saved migration plan")
def get_plan(project_id: str, db: Session = Depends(get_db)):
    """Get the previously generated migration plan for a project."""
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    plan = project.get("migration_plan")
    if not plan:
        raise HTTPException(
            status_code=404,
            detail="No plan found. Run POST /projects/{id}/plan first."
        )
    return plan
