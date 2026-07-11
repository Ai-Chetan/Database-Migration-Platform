"""
Policy Engine Router
File: migration/backend/connector_framework/routers/policy.py

Endpoints:
    POST /policies                       → create a policy rule
    GET  /policies                       → list policies for tenant
    GET  /policies/{id}                  → get policy detail
    PUT  /policies/{id}/toggle           → enable/disable a policy
    DELETE /policies/{id}                → delete a policy
    POST /policies/check/{job_id}        → run all policy checks against a job
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.connector_framework.policy.policy_engine import PolicyEngine

router = APIRouter(tags=["Policy Engine"])
engine = PolicyEngine()


class CreatePolicyRequest(BaseModel):
    tenant_id:   str = "local"
    name:        str
    policy_type: str
    config:      Optional[Dict[str, Any]] = None
    is_active:   bool = True


class CheckPolicyRequest(BaseModel):
    tenant_id:       str = "local"
    dry_run_result:  Optional[Dict[str, Any]] = None
    approval_status: Optional[str] = None


@router.post("/policies", summary="Create an organizational policy rule")
def create_policy(req: CreatePolicyRequest, db: Session = Depends(get_db)):
    """
    Create a policy that gets enforced before migrations execute.

    Supported policy_type values:
      forbidden_lossy_conversion    → block migrations with unsafe type conversions
        config: {"severity": "block", "block_lossy": true}

      require_approval              → block until migration is approved
        config: {"roles": ["tenant_admin", "migration_admin"]}

      max_downtime_minutes          → warn/block if estimated downtime too high
        config: {"max_minutes": 30, "severity": "warn"}

      require_validation            → block if validation rules aren't configured
        config: {"severity": "block"}

      max_chunk_size                → enforce maximum rows per chunk
        config: {"max_rows": 200000, "severity": "warn"}

      require_backup_before_cutover → warn before CDC cutover without backup
        config: {"severity": "warn"}

      forbidden_table_drop          → block if migration drops source tables
        config: {"severity": "block"}

    Example:
    {
      "name": "No unsafe conversions in production",
      "policy_type": "forbidden_lossy_conversion",
      "config": {"severity": "block", "block_lossy": false}
    }
    """
    valid_types = {
        "forbidden_lossy_conversion", "require_approval", "max_downtime_minutes",
        "require_validation", "forbidden_table_drop", "require_backup_before_cutover",
        "max_chunk_size",
    }
    if req.policy_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid policy_type. Must be one of: {sorted(valid_types)}"
        )

    return engine.create_policy(
        db=db,
        tenant_id=req.tenant_id,
        name=req.name,
        policy_type=req.policy_type,
        config=req.config,
        is_active=req.is_active,
    )


@router.get("/policies", summary="List all policies for a tenant")
def list_policies(tenant_id: str = "local", db: Session = Depends(get_db)):
    return engine.list_policies(db, tenant_id)


@router.get("/policies/{policy_id}", summary="Get policy detail")
def get_policy(policy_id: str, db: Session = Depends(get_db)):
    policy = engine.get_policy(db, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return policy


@router.put("/policies/{policy_id}/toggle", summary="Enable or disable a policy")
def toggle_policy(policy_id: str, is_active: bool, db: Session = Depends(get_db)):
    policy = engine.get_policy(db, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return engine.toggle_policy(db, policy_id, is_active)


@router.delete("/policies/{policy_id}", summary="Delete a policy")
def delete_policy(policy_id: str, db: Session = Depends(get_db)):
    policy = engine.get_policy(db, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return engine.delete_policy(db, policy_id)


@router.post("/policies/check/{job_id}", summary="Run all policy checks against a job")
def check_policies(job_id: str, req: CheckPolicyRequest, db: Session = Depends(get_db)):
    """
    Run all active policies for the tenant against a specific migration job.

    Call this:
      - After dry-run completes (pass dry_run_result)
      - Before allowing job execution to start
      - As part of the approval workflow

    Returns passed=False if any blocking policy is violated.
    Job execution should be prevented if passed=False.

    Response:
    {
      "passed": false,
      "violations": [
        {"policy": "No unsafe conversions", "type": "forbidden_lossy_conversion",
         "severity": "block", "message": "2 unsafe type conversion(s) found..."}
      ],
      "warnings": [...],
      "total_violations": 1,
      "total_warnings": 0
    }
    """
    result = engine.check_all(
        db=db,
        tenant_id=req.tenant_id,
        job_id=job_id,
        dry_run_result=req.dry_run_result,
        approval_status=req.approval_status,
    )
    return result.to_dict()
