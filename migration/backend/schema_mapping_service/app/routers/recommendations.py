"""
Recommendations Router
File: migration/backend/schema_mapping_service/app/routers/recommendations.py

Endpoints:
    POST /projects/{id}/recommend          → run auto-recommender
    GET  /projects/{id}/recommendations    → list saved recommendations
    POST /projects/{id}/recommendations/accept  → accept a set of recommendations
    POST /projects/{id}/recommendations/reject  → reject a set of recommendations
    POST /projects/{id}/recommendations/apply   → apply accepted → create table/column mappings
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.recommendation_engine.recommender import RecommendationEngine
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.datatype.type_engine import DataTypeEngine
from backend.schema_mapping_service.app.schemas.schemas import AcceptRecommendationsRequest

router = APIRouter(prefix="/projects", tags=["Recommendations"])
repo   = MappingRepository()
engine = RecommendationEngine()


@router.post("/{project_id}/recommend", summary="Run recommendation engine for a project")
def run_recommendations(project_id: str, db: Session = Depends(get_db)):
    """
    Runs the intelligent recommendation engine to auto-suggest:
      - Table matches (exact, fuzzy, alias)
      - Column matches (exact, fuzzy, alias, prefix/suffix)
      - Rename candidates (when names differ but are similar)

    Results are saved to schema_recommendations and returned sorted
    by confidence score descending.

    The user reviews these in the UI and accepts/rejects each one.
    Accepted ones become actual table/column mappings via /apply.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    if not src_version or not tgt_version:
        raise HTTPException(status_code=404, detail="Schema versions not found")

    recs = engine.recommend(
        source_schema=src_version["schema_data"],
        target_schema=tgt_version["schema_data"],
    )

    rec_dicts = [r.to_dict() for r in recs]
    repo.save_recommendations(db, project_id, rec_dicts)

    return {
        "project_id":    project_id,
        "total":         len(recs),
        "table_matches": sum(1 for r in recs if r.rec_type == "table_match"),
        "column_matches": sum(1 for r in recs if r.rec_type == "column_match"),
        "rename_candidates": sum(1 for r in recs if r.rec_type == "rename_candidate"),
        "recommendations": rec_dicts,
    }


@router.get("/{project_id}/recommendations", summary="List saved recommendations")
def list_recommendations(project_id: str, db: Session = Depends(get_db)):
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    recs = repo.list_recommendations(db, project_id)
    for r in recs:
        r["id"] = f"{r['source_ref']}→{r['target_ref']}"
    return recs


@router.post("/{project_id}/recommendations/accept", summary="Accept recommendations")
def accept_recommendations(
    project_id: str,
    req: AcceptRecommendationsRequest,
    db: Session = Depends(get_db)
):
    """
    Mark recommendations as accepted.
    rec_ids format: ["users→customers", "users.first_name→customers.fname"]
    Call /apply afterwards to convert them into actual mappings.
    """
    repo.accept_recommendations(db, project_id, req.rec_ids)
    return {"accepted": len(req.rec_ids), "rec_ids": req.rec_ids}


@router.post("/{project_id}/recommendations/reject", summary="Reject recommendations")
def reject_recommendations(
    project_id: str,
    req: AcceptRecommendationsRequest,
    db: Session = Depends(get_db)
):
    repo.reject_recommendations(db, project_id, req.rec_ids)
    return {"rejected": len(req.rec_ids), "rec_ids": req.rec_ids}


@router.post("/{project_id}/recommendations/apply", summary="Apply accepted recommendations as mappings")
def apply_recommendations(project_id: str, db: Session = Depends(get_db)):
    """
    Converts all accepted recommendations into actual table and column mappings.

    For each accepted table_match:
      → Creates a schema_table_mapping record (type: single)

    For each accepted column_match or rename_candidate under that table:
      → Creates schema_column_mapping records with auto-computed type safety

    Returns counts of what was created.
    """
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    src_version = repo.get_schema_version(db, project["source_schema_id"])
    tgt_version = repo.get_schema_version(db, project["target_schema_id"])
    src_schema  = src_version["schema_data"]
    tgt_schema  = tgt_version["schema_data"]

    all_recs = repo.list_recommendations(db, project_id)
    accepted = [r for r in all_recs if r.get("accepted") is True]

    table_recs  = [r for r in accepted if r["rec_type"] == "table_match"]
    col_recs    = [r for r in accepted if r["rec_type"] in ("column_match", "rename_candidate")]

    type_engine = DataTypeEngine(db=db)
    tables_created = 0
    cols_created   = 0

    for tr in table_recs:
        src_tbl = tr["source_ref"]
        tgt_tbl = tr["target_ref"]

        # Create table mapping
        tm = repo.save_table_mapping(
            db=db,
            project_id=project_id,
            mapping_type="single",
            source_tables=[src_tbl],
            target_tables=[tgt_tbl],
        )
        tables_created += 1

        # Find column recs for this table pair and create column mappings
        matching_col_recs = [
            r for r in col_recs
            if r["source_ref"].startswith(src_tbl + ".") and
               r["target_ref"].startswith(tgt_tbl + ".")
        ]

        col_mappings = []
        for cr in matching_col_recs:
            _, src_col = cr["source_ref"].rsplit(".", 1)
            _, tgt_col = cr["target_ref"].rsplit(".", 1)

            src_type = src_schema["tables"].get(src_tbl, {}).get("columns", {}).get(src_col, {}).get("type", "")
            tgt_type = tgt_schema["tables"].get(tgt_tbl, {}).get("columns", {}).get(tgt_col, {}).get("type", "")

            safety, requires_cast, cast_expr = None, False, None
            if src_type and tgt_type:
                col_ref = f"`{src_tbl}`.`{src_col}`"
                result  = type_engine.analyze(col_ref, src_type, tgt_type)
                safety, requires_cast, cast_expr = result.safety, result.requires_cast, result.cast_expression

            col_mappings.append({
                "source_table": src_tbl, "source_column": src_col, "source_type": src_type,
                "target_table": tgt_tbl, "target_column": tgt_col, "target_type": tgt_type,
                "mapping_kind": "rename" if src_col != tgt_col else "direct",
                "mapping_config": None,
                "conversion_safety": safety, "requires_cast": requires_cast,
                "cast_expression": cast_expr,
            })

        if col_mappings:
            repo.bulk_save_column_mappings(db, tm["id"], col_mappings)
            cols_created += len(col_mappings)

    return {
        "project_id":    project_id,
        "tables_created": tables_created,
        "columns_created": cols_created,
    }
