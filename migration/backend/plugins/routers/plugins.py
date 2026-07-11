"""
Plugin Refactor Router
File: migration/backend/plugins/routers/plugins.py

Endpoints:
    GET  /plugins/types                      → list all plugin types
    GET  /plugins/{type}                     → list plugins of one type
    POST /plugins/validators/run             → run a validator against a chunk
    POST /plugins/notifiers/configure        → configure a notification provider
    POST /plugins/notifiers/test             → test a notification provider
    GET  /plugins/notifiers/active           → list active notifiers
    POST /plugins/policies/check/{job_id}    → run all policies for a job
    POST /plugins/policies/configure         → configure a policy
    GET  /plugins/transformers/kinds         → list available mapping_kinds
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import datetime
import uuid

from backend.shared.config.database import get_db
from backend.plugins.policy.policy_plugins import PolicyRunner, register_all_policies
from backend.plugins.notifiers.notifier_plugins import (
    NotificationManager, register_all_notifiers,
    EmailNotifier, SlackNotifier, TeamsNotifier, WebhookNotifier, PagerDutyNotifier
)

router = APIRouter(prefix="/plugins", tags=["Plugin Refactor"])


# ── Request models ─────────────────────────────────────────────────────────────

class RunValidatorRequest(BaseModel):
    validator_name:   str
    source_config:    Dict[str, Any]
    target_config:    Dict[str, Any]
    table_name:       str
    pk_column:        str
    pk_start:         Any
    pk_end:           Any
    validator_config: Optional[Dict[str, Any]] = None


class ConfigureNotifierRequest(BaseModel):
    provider:        str   # email | slack | teams | webhook | pagerduty
    config:          Dict[str, Any]
    tenant_id:       str = "local"
    subscribed_events: Optional[List[str]] = None


class TestNotifierRequest(BaseModel):
    provider: str
    config:   Dict[str, Any]


class PolicyCheckRequest(BaseModel):
    tenant_id:       str = "local"
    dry_run_result:  Optional[Dict[str, Any]] = None
    approval_status: Optional[str] = None
    context:         Optional[Dict[str, Any]] = None


class ConfigurePolicyRequest(BaseModel):
    tenant_id:   str = "local"
    name:        str
    policy_type: str
    config:      Optional[Dict[str, Any]] = None
    is_active:   bool = True


# ── Plugin discovery ───────────────────────────────────────────────────────────

@router.get("/types", summary="List all plugin types")
def list_plugin_types():
    """Returns all registered plugin types in the platform kernel."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager
        return {"plugin_types": PluginManager.plugin_types()}
    except Exception as e:
        return {"plugin_types": ["validator", "transformer", "notifier", "policy",
                                 "connector", "workflow_node"], "note": str(e)}


@router.get("/{plugin_type}", summary="List registered plugins of one type")
def list_plugins_by_type(plugin_type: str):
    """
    List all registered plugins for a given type.

    Valid types: validator | transformer | notifier | policy |
                 connector | workflow_node | assessment | scheduler |
                 report | ai | storage | security | monitoring
    """
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager
        plugins = PluginManager.list_plugins(plugin_type)
        if not plugins:
            return {
                "plugin_type": plugin_type,
                "count":       0,
                "plugins":     [],
                "note":        f"No plugins registered for type '{plugin_type}'. "
                               "This service may not have started yet."
            }
        return {"plugin_type": plugin_type, "count": len(plugins), "plugins": plugins}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Validator endpoints ────────────────────────────────────────────────────────

@router.post("/validators/run", summary="Run a validator against a chunk")
def run_validator(req: RunValidatorRequest):
    """
    Run any registered validator against a PK range on source and target.

    Available validators:
      row_count_validator     → row counts match
      checksum_validator      → MD5 checksums match
      sample_validator        → random sample of rows match
      null_check_validator    → no unexpected NULLs
      business_rule_validator → custom SQL rule returns 0 rows

    Returns ValidationResult with passed=True/False and details.
    """
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry

        validator = PluginManager.get(
            PluginType.VALIDATOR, req.validator_name, req.validator_config or {}
        )
        src = ConnectorRegistry.get_for_config(req.source_config)
        tgt = ConnectorRegistry.get_for_config(req.target_config)
        src.connect()
        tgt.connect()
        try:
            result = validator.validate(
                src, tgt, req.table_name, req.pk_column, req.pk_start, req.pk_end
            )
        finally:
            src.disconnect()
            tgt.disconnect()

        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


@router.get("/validators/list", summary="List all available validators")
def list_validators():
    return {
        "validators": [
            {"name": "row_count_validator",     "description": "Verifies row counts match source and target"},
            {"name": "checksum_validator",       "description": "MD5 checksum comparison of all row data"},
            {"name": "sample_validator",         "description": "Random sample comparison field-by-field"},
            {"name": "null_check_validator",     "description": "No NULLs in required columns on target"},
            {"name": "business_rule_validator",  "description": "Custom SQL WHERE clause returns 0 rows"},
        ]
    }


# ── Transformer endpoints ──────────────────────────────────────────────────────

@router.get("/transformers/kinds", summary="List available transformation mapping_kinds")
def list_transformer_kinds():
    """
    Returns all available mapping_kind values for schema_column_mappings.
    Each kind corresponds to a registered TransformerPlugin.
    """
    return {
        "mapping_kinds": [
            {"kind": "direct",      "description": "Copy value as-is (no transformation)"},
            {"kind": "rename",      "description": "Copy from a differently-named source column"},
            {"kind": "constant",    "description": "Always write a fixed literal value"},
            {"kind": "expression",  "description": "Python expression with access to `value` and `row`"},
            {"kind": "transform",   "description": "Apply a named function: upper/lower/strip/int/float/date_format/truncate"},
            {"kind": "lookup",      "description": "Replace value from a static dict mapping"},
            {"kind": "mask",        "description": "Part 7: masking (hash/redact/partial/encrypt/nullify)"},
            {"kind": "synthesize",  "description": "Part 7: synthetic data (fake_email/fake_name/etc.)"},
        ]
    }


# ── Notifier endpoints ─────────────────────────────────────────────────────────

@router.post("/notifiers/configure", summary="Configure and activate a notification provider")
def configure_notifier(req: ConfigureNotifierRequest, db: Session = Depends(get_db)):
    """
    Configure a notification provider and activate it.
    Config is stored in the database (encrypted if it contains credentials).

    provider options: email | slack | teams | webhook | pagerduty

    After configuring, the notifier automatically subscribes to the Event Bus
    and will send notifications for the subscribed events.

    Example (Slack):
    {
      "provider": "slack",
      "config": {"webhook_url": "https://hooks.slack.com/services/..."},
      "subscribed_events": ["job.failed", "drift.detected", "job.completed"]
    }
    """
    valid_providers = {"email", "slack", "teams", "webhook", "pagerduty"}
    if req.provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider '{req.provider}'. Valid: {sorted(valid_providers)}"
        )

    config = dict(req.config)
    if req.subscribed_events:
        config["subscribed_events"] = req.subscribed_events

    # Store in DB (secrets_vault for sensitive configs)
    try:
        key_name = f"notifier_{req.provider}_{req.tenant_id}"
        db.execute(
            text("""
                INSERT INTO secrets_vault (id, tenant_id, key_name, encrypted_value, description, created_at)
                VALUES (gen_random_uuid(), :tid, :key, :val, :desc, :now)
                ON CONFLICT (tenant_id, key_name)
                DO UPDATE SET encrypted_value=:val, description=:desc
            """),
            {
                "tid":  req.tenant_id,
                "key":  key_name,
                "val":  json.dumps(config),
                "desc": f"{req.provider} notification provider config",
                "now":  datetime.datetime.utcnow(),
            }
        )
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store config: {e}")

    # Activate the notifier
    provider_map = {
        "email": EmailNotifier, "slack": SlackNotifier,
        "teams": TeamsNotifier, "webhook": WebhookNotifier,
        "pagerduty": PagerDutyNotifier,
    }
    notifier_cls = provider_map[req.provider]
    notifier     = notifier_cls(config)
    NotificationManager.register_notifier(notifier)

    return {
        "provider":   req.provider,
        "status":     "configured",
        "subscribed_events": req.subscribed_events or ["all"],
        "message":    f"{req.provider.title()} notifier activated. "
                      "It will now receive events from the Event Bus.",
    }


@router.post("/notifiers/test", summary="Send a test notification")
def test_notifier(req: TestNotifierRequest):
    """Send a test event through a notification provider to verify config."""
    provider_map = {
        "email": EmailNotifier, "slack": SlackNotifier,
        "teams": TeamsNotifier, "webhook": WebhookNotifier,
        "pagerduty": PagerDutyNotifier,
    }
    if req.provider not in provider_map:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    test_event = {
        "event_type":    "job.completed",
        "source_service": "plugin_service",
        "resource_type": "job",
        "resource_id":   "test-job-00000000",
        "payload":       {"rows_written": 1000000, "table_name": "test_table"},
        "published_at":  datetime.datetime.utcnow().isoformat(),
    }

    notifier = provider_map[req.provider](req.config)
    success  = notifier.send(test_event)
    return {
        "provider": req.provider,
        "success":  success,
        "test_event": test_event,
        "message": "Test notification sent successfully" if success else
                   "Test notification failed — check provider config",
    }


# ── Policy endpoints ───────────────────────────────────────────────────────────

@router.post("/policies/check/{job_id}", summary="Run all active policies for a job")
def run_policy_check(job_id: str, req: PolicyCheckRequest, db: Session = Depends(get_db)):
    """
    Run all active organizational policies for a tenant against a job.
    Returns passed=false if any blocking policy is violated.
    Migration execution should be prevented if can_proceed=false.

    Typically called:
      1. After dry-run completes (supply dry_run_result)
      2. Before allowing job execution to start
      3. As part of the approval workflow
    """
    return PolicyRunner.check_all(
        db=db,
        job_id=job_id,
        tenant_id=req.tenant_id,
        dry_run_result=req.dry_run_result,
        approval_status=req.approval_status,
        context=req.context,
    )


@router.post("/policies/configure", summary="Create or update a policy rule")
def configure_policy(req: ConfigurePolicyRequest, db: Session = Depends(get_db)):
    """
    Create an organizational policy rule.

    policy_type options:
      forbidden_lossy_conversion    → block unsafe type conversions
      require_approval              → block until approved
      max_downtime_minutes          → warn/block if duration too high
      require_validation            → block if no validation configured
      max_chunk_size                → enforce max rows/chunk
      require_backup_before_cutover → warn before CDC cutover
      require_masking_for_pii       → block if PII columns lack masking
    """
    valid_types = {
        "forbidden_lossy_conversion", "require_approval", "max_downtime_minutes",
        "require_validation", "max_chunk_size", "require_backup_before_cutover",
        "require_masking_for_pii",
    }
    if req.policy_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid policy_type. Must be one of: {sorted(valid_types)}"
        )

    pid = str(uuid.uuid4())
    db.execute(
        text("""
            INSERT INTO policy_rules (id, tenant_id, name, policy_type, config, is_active, created_at)
            VALUES (:id, :tid, :name, :ptype, :config::jsonb, :active, :now)
            ON CONFLICT DO NOTHING
        """),
        {
            "id":     pid, "tid": req.tenant_id, "name": req.name,
            "ptype":  req.policy_type, "config": json.dumps(req.config or {}),
            "active": req.is_active, "now": datetime.datetime.utcnow(),
        }
    )
    db.commit()
    return {"id": pid, "name": req.name, "policy_type": req.policy_type,
            "is_active": req.is_active}


@router.get("/policies/list", summary="List available policy types")
def list_policy_types():
    return {
        "policy_types": [
            {"name": "forbidden_lossy_conversion",    "default_severity": "block"},
            {"name": "require_approval",              "default_severity": "block"},
            {"name": "max_downtime_minutes",          "default_severity": "warn"},
            {"name": "require_validation",            "default_severity": "warn"},
            {"name": "max_chunk_size",                "default_severity": "warn"},
            {"name": "require_backup_before_cutover", "default_severity": "warn"},
            {"name": "require_masking_for_pii",       "default_severity": "block"},
        ]
    }
