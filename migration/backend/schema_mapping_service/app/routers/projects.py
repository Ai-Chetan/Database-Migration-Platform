"""
Projects Router
File: migration/backend/schema_mapping_service/app/routers/projects.py

Endpoints:
    POST /projects              → create project
    GET  /projects              → list projects
    GET  /projects/{id}         → get project detail
    PUT  /projects/{id}/status  → update status

CHANGES IN THIS VERSION (Stage 1 audit fix):
  Column-level code was already correct (mapping_repository.py matches
  the real schema exactly). The gap was the same architectural one found
  everywhere else in Phase B: zero authentication, tenant_id taken from
  client-supplied request fields instead of the authenticated user, and
  no ownership check on get_project/update_status - any authenticated
  user could view or modify another tenant's mapping project by
  guessing a UUID.

  Note: CreateProjectRequest still has a tenant_id field in schemas.py
  (left in place rather than risk touching a shared file used by other
  routers too) - but it's now ignored here in favor of the authenticated
  user's real tenant_id, so a client can no longer spoof it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.schemas.schemas import (
    CreateProjectRequest, ProjectResponse
)

router = APIRouter(prefix="/projects", tags=["Mapping Projects"])
repo   = MappingRepository()


def _owned_project(db: Session, project_id: str, user: CurrentUser) -> dict:
    project = repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    if not user.can("*") and project.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


@router.post("", summary="Create a new mapping project")
def create_project(
    req: CreateProjectRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:write")),
):
    """
    A project links one source schema version to one target schema version.
    All table and column mappings belong to the project.
    """
    src = repo.get_schema_version(db, req.source_schema_id)
    tgt = repo.get_schema_version(db, req.target_schema_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source schema {req.source_schema_id} not found")
    if not tgt:
        raise HTTPException(status_code=404, detail=f"Target schema {req.target_schema_id} not found")

    return repo.create_project(
        db=db,
        tenant_id=user.tenant_id,
        name=req.name,
        source_schema_id=req.source_schema_id,
        target_schema_id=req.target_schema_id,
        description=req.description,
    )


@router.get("", summary="List mapping projects for your tenant")
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:read")),
):
    return repo.list_projects(db, user.tenant_id)


@router.get("/{project_id}", summary="Get project detail")
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:read")),
):
    return _owned_project(db, project_id, user)


@router.put("/{project_id}/status", summary="Update project status")
def update_status(
    project_id: str, status: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:write")),
):
    valid = {"draft", "ready", "executing", "done", "failed"}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid}")
    _owned_project(db, project_id, user)
    repo.update_project_status(db, project_id, status)
    return {"project_id": project_id, "status": status}
