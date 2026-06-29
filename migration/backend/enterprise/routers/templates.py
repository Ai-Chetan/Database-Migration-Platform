"""
Migration Templates Router
File: migration/backend/enterprise/routers/templates.py

Endpoints:
    POST /templates                    → save a migration template
    GET  /templates                    → list templates (tenant + public)
    GET  /templates/{id}               → get template detail
    POST /templates/{id}/apply/{job}   → apply template to a job
    DELETE /templates/{id}             → delete template
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.enterprise.security.audit.audit_trail import AuditTrail
from backend.enterprise.saas.templates.template_service import TemplateService

router       = APIRouter(prefix="/templates", tags=["Migration Templates"])
template_svc = TemplateService()


class SaveTemplateRequest(BaseModel):
    name:             str
    description:      Optional[str] = None
    source_db_type:   str = "mysql"
    target_db_type:   str = "postgresql"
    table_mappings:   Optional[Dict[str, Any]] = None
    chunk_config:     Optional[Dict[str, Any]] = None
    validation_rules: Optional[List[Any]] = None
    execution_config: Optional[Dict[str, Any]] = None
    tags:             Optional[List[str]] = None
    is_public:        bool = False


@router.post("", summary="Save a migration configuration as a template")
def save_template(
    req:     SaveTemplateRequest,
    request: Request,
    user:    CurrentUser = Depends(require_permission("jobs:create")),
    db:      Session     = Depends(get_db),
):
    """
    Save the current migration configuration as a reusable template.

    A template captures:
      - Table and column mappings (single/split/merge)
      - Chunk configuration (chunk size strategy, max workers)
      - Validation rules (row_count, checksum, sample)
      - Execution settings

    After saving, apply to future jobs with POST /templates/{id}/apply/{job_id}.

    Set is_public=true to share with all tenants on the platform.

    Example use cases:
      - MySQL 5.7 → PostgreSQL 14 standard mapping template
      - ERP source schema → normalized target schema template
      - Quarterly data warehouse refresh template
    """
    try:
        result = template_svc.save_template(
            db=db,
            tenant_id=user.tenant_id,
            created_by_id=user.user_id,
            name=req.name,
            description=req.description,
            source_db_type=req.source_db_type,
            target_db_type=req.target_db_type,
            table_mappings=req.table_mappings,
            chunk_config=req.chunk_config,
            validation_rules=req.validation_rules,
            execution_config=req.execution_config,
            tags=req.tags,
            is_public=req.is_public,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="template.create",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="template", resource_id=result["id"],
        new_value={"name": req.name}, request=request,
    )
    return result


@router.get("", summary="List available templates")
def list_templates(
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    """
    Returns all templates for your tenant plus all public platform templates.
    Sorted by usage_count descending — most-used templates first.
    """
    return template_svc.list_templates(db, user.tenant_id)


@router.get("/{template_id}", summary="Get template detail")
def get_template(
    template_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    template = template_svc.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    # Only owner tenant and platform admin can see private templates
    if not template.get("is_public") and template.get("tenant_id") != user.tenant_id:
        if not user.can("*"):
            raise HTTPException(status_code=403, detail="Access denied")
    return template


@router.post("/{template_id}/apply/{job_id}", summary="Apply template to a job")
def apply_template(
    template_id: str,
    job_id:      str,
    request:     Request,
    user:        CurrentUser = Depends(require_permission("jobs:update")),
    db:          Session     = Depends(get_db),
):
    """
    Apply a saved template to an existing migration job.

    This pre-fills:
      - All table and column mappings from the template
      - Chunk size configuration
      - Validation rules

    After applying, you can still override individual mappings
    via the schema mapping service before running the migration.
    """
    try:
        result = template_svc.apply_template(db=db, template_id=template_id, job_id=job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    AuditTrail.log(
        db=db, action="template.apply",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="job", resource_id=job_id,
        new_value={"template_id": template_id},
        request=request,
    )
    return result


@router.delete("/{template_id}", summary="Delete a template")
def delete_template(
    template_id: str,
    request:     Request,
    user:        CurrentUser = Depends(require_permission("jobs:delete")),
    db:          Session     = Depends(get_db),
):
    template = template_svc.get_template(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
    if template.get("tenant_id") != user.tenant_id and not user.can("*"):
        raise HTTPException(status_code=403, detail="Cannot delete another tenant's template")

    result = template_svc.delete_template(db, template_id, user.tenant_id)
    AuditTrail.log(
        db=db, action="template.delete",
        tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="template", resource_id=template_id,
        request=request,
    )
    return result
