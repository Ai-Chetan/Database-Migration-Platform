"""
Projects Router
File: migration/backend/schema_mapping_service/app/routers/projects.py

Endpoints:
    POST /projects              → create project
    GET  /projects              → list projects
    GET  /projects/{id}         → get project detail
    PUT  /projects/{id}/status  → update status
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.schemas.schemas import (
    CreateProjectRequest, ProjectResponse
)

router = APIRouter(prefix="/projects", tags=["Mapping Projects"])
repo   = MappingRepository()


@router.post("", summary="Create a new mapping project")
def create_project(req: CreateProjectRequest, db: Session = Depends(get_db)):
    """
    A project links one source schema version to one target schema version.
    All table and column mappings belong to the project.
    """
    # Validate both schema versions exist
    src = repo.get_schema_version(db, req.source_schema_id)
    tgt = repo.get_schema_version(db, req.target_schema_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source schema {req.source_schema_id} not found")
    if not tgt:
        raise HTTPException(status_code=404, detail=f"Target schema {req.target_schema_id} not found")

    return repo.create_project(
        db=db,
        tenant_id=req.tenant_id,
        name=req.name,
        source_schema_id=req.source_schema_id,
        target_schema_id=req.target_schema_id,
        description=req.description,
    )


@router.get("", summary="List all mapping projects")
def list_projects(tenant_id: str = "local", db: Session = Depends(get_db)):
    return repo.list_projects(db, tenant_id)


@router.get("/{project_id}", summary="Get project detail")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


@router.put("/{project_id}/status", summary="Update project status")
def update_status(project_id: str, status: str, db: Session = Depends(get_db)):
    valid = {"draft", "ready", "executing", "done", "failed"}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid}")
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    repo.update_project_status(db, project_id, status)
    return {"project_id": project_id, "status": status}