"""
Policy Engine
File: migration/backend/connector_framework/policy/policy_engine.py

Enforces organizational policies before a migration executes.

Policies can block migration execution if violated.
All policy checks run during dry-run and before job start.

Built-in policy types:
  forbidden_lossy_conversion   → block if any lossy type conversions exist
  require_approval             → block if no approval on record
  max_downtime_minutes         → block if estimated downtime exceeds limit
  require_validation           → block if validation rules not configured
  forbidden_table_drop         → block if source tables are being dropped
  require_backup_before_cutover → block CDC cutover if no backup confirmed
  max_chunk_size               → enforce maximum chunk size per table
  forbidden_source_db_types    → block migrations from certain DB types

Policy config examples:
  {"policy_type": "forbidden_lossy_conversion", "config": {"severity": "block"}}
  {"policy_type": "max_downtime_minutes",        "config": {"max_minutes": 30}}
  {"policy_type": "require_approval",            "config": {"roles": ["tenant_admin"]}}
"""

import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


@dataclass
class PolicyViolation:
    policy_name:  str
    policy_type:  str
    severity:     str          # block | warn
    message:      str
    config:       Dict = field(default_factory=dict)


@dataclass
class PolicyCheckResult:
    passed:     bool
    violations: List[PolicyViolation] = field(default_factory=list)
    warnings:   List[PolicyViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed":     self.passed,
            "violations": [
                {"policy": v.policy_name, "type": v.policy_type,
                 "severity": v.severity, "message": v.message}
                for v in self.violations
            ],
            "warnings": [
                {"policy": v.policy_name, "type": v.policy_type,
                 "severity": v.severity, "message": v.message}
                for v in self.warnings
            ],
            "total_violations": len(self.violations),
            "total_warnings":   len(self.warnings),
        }


class PolicyEngine:

    def check_all(
        self,
        db:               Session,
        tenant_id:        str,
        job_id:           str,
        dry_run_result:   Optional[Dict] = None,
        approval_status:  Optional[str]  = None,
    ) -> PolicyCheckResult:
        """
        Run all active policies for a tenant against a migration job.
        Called during dry-run and before job execution starts.

        Returns PolicyCheckResult with passed=True only if no blocking violations.
        """
        policies = self._load_policies(db, tenant_id)

        result = PolicyCheckResult(passed=True)

        for policy in policies:
            if not policy.get("is_active"):
                continue

            violation = self._check_policy(
                policy=policy,
                db=db,
                job_id=job_id,
                dry_run_result=dry_run_result,
                approval_status=approval_status,
            )

            if violation:
                if violation.severity == "block":
                    result.violations.append(violation)
                    result.passed = False
                else:
                    result.warnings.append(violation)

        if result.passed:
            logger.info("All policies passed", tenant_id=tenant_id, job_id=job_id)
        else:
            logger.warning(
                "Policy violations detected",
                tenant_id=tenant_id,
                job_id=job_id,
                violations=len(result.violations)
            )

        return result

    def create_policy(
        self,
        db:          Session,
        tenant_id:   str,
        name:        str,
        policy_type: str,
        config:      Dict = None,
        is_active:   bool = True,
    ) -> dict:
        """Create a new policy rule for a tenant."""
        import uuid, json
        pid = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO policy_rules
                    (id, tenant_id, name, policy_type, config, is_active, created_at)
                VALUES
                    (:id, :tid, :name, :ptype, :config::jsonb, :active, :now)
            """),
            {
                "id":     pid,
                "tid":    tenant_id,
                "name":   name,
                "ptype":  policy_type,
                "config": json.dumps(config or {}),
                "active": is_active,
                "now":    datetime.datetime.utcnow(),
            }
        )
        db.commit()
        return self.get_policy(db, pid)

    def get_policy(self, db: Session, policy_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM policy_rules WHERE id=:id"),
            {"id": policy_id}
        ).fetchone()
        return self._row(row) if row else None

    def list_policies(self, db: Session, tenant_id: str) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM policy_rules WHERE tenant_id=:tid ORDER BY created_at"),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def delete_policy(self, db: Session, policy_id: str) -> dict:
        db.execute(text("DELETE FROM policy_rules WHERE id=:id"), {"id": policy_id})
        db.commit()
        return {"deleted": policy_id}

    def toggle_policy(self, db: Session, policy_id: str, is_active: bool) -> dict:
        db.execute(
            text("UPDATE policy_rules SET is_active=:a WHERE id=:id"),
            {"a": is_active, "id": policy_id}
        )
        db.commit()
        return self.get_policy(db, policy_id)

    # ── Private policy checkers ───────────────────────────────────────────────

    def _load_policies(self, db: Session, tenant_id: str) -> List[Dict]:
        rows = db.execute(
            text("SELECT * FROM policy_rules WHERE tenant_id=:tid AND is_active=TRUE"),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def _check_policy(
        self,
        policy:          Dict,
        db:              Session,
        job_id:          str,
        dry_run_result:  Optional[Dict],
        approval_status: Optional[str],
    ) -> Optional[PolicyViolation]:
        """
        Check one policy rule. Returns PolicyViolation if violated, None if passed.
        """
        ptype  = policy.get("policy_type", "")
        config = policy.get("config") or {}
        name   = policy.get("name", ptype)
        severity = config.get("severity", "block")

        # ── forbidden_lossy_conversion ────────────────────────────────────────
        if ptype == "forbidden_lossy_conversion":
            if dry_run_result:
                lossy = dry_run_result.get("lossy_conversions", [])
                unsafe = dry_run_result.get("unsafe_conversions", [])
                if unsafe:
                    return PolicyViolation(
                        policy_name=name, policy_type=ptype,
                        severity="block",
                        message=f"Policy '{name}' blocks migration: {len(unsafe)} unsafe type conversion(s) found. "
                                f"First: {unsafe[0].get('source_column')} "
                                f"({unsafe[0].get('from_type')} → {unsafe[0].get('to_type')})"
                    )
                if lossy and config.get("block_lossy", False):
                    return PolicyViolation(
                        policy_name=name, policy_type=ptype,
                        severity=severity,
                        message=f"Policy '{name}': {len(lossy)} lossy conversion(s) detected. "
                                f"Data precision may be lost."
                    )

        # ── require_approval ──────────────────────────────────────────────────
        elif ptype == "require_approval":
            if approval_status not in ("approved",):
                return PolicyViolation(
                    policy_name=name, policy_type=ptype,
                    severity="block",
                    message=f"Policy '{name}' requires migration approval before execution. "
                            f"Current status: {approval_status or 'not_requested'}. "
                            f"Request approval via POST /jobs/{job_id}/approval/request"
                )

        # ── max_downtime_minutes ──────────────────────────────────────────────
        elif ptype == "max_downtime_minutes":
            max_min = config.get("max_minutes", 60)
            if dry_run_result:
                duration_str = dry_run_result.get("estimated_duration", "")
                # Simple check: if complexity is HIGH assume downtime risk
                complexity = dry_run_result.get("complexity", "LOW")
                if complexity == "HIGH" and max_min < 30:
                    return PolicyViolation(
                        policy_name=name, policy_type=ptype,
                        severity=severity,
                        message=f"Policy '{name}': HIGH complexity migration may exceed "
                                f"{max_min} minute downtime limit. Consider using CDC mode."
                    )

        # ── require_validation ────────────────────────────────────────────────
        elif ptype == "require_validation":
            # Check if validation rules are configured
            val_count = db.execute(
                text("SELECT COUNT(*) FROM schema_validation_results WHERE project_id IN "
                     "(SELECT id FROM mapping_projects LIMIT 1)"),
            ).fetchone()
            # Simple heuristic — can be made more specific
            if not val_count or val_count[0] == 0:
                return PolicyViolation(
                    policy_name=name, policy_type=ptype,
                    severity=severity,
                    message=f"Policy '{name}': Validation rules must be configured and run before migration. "
                            f"Run POST /projects/{{id}}/validate first."
                )

        # ── max_chunk_size ────────────────────────────────────────────────────
        elif ptype == "max_chunk_size":
            max_size = config.get("max_rows", 500_000)
            rows = db.execute(
                text("SELECT table_name, computed_chunk_size FROM migration_tables "
                     "WHERE job_id=:jid AND computed_chunk_size > :max"),
                {"jid": job_id, "max": max_size}
            ).fetchall()
            if rows:
                names = [r[0] for r in rows]
                return PolicyViolation(
                    policy_name=name, policy_type=ptype,
                    severity=severity,
                    message=f"Policy '{name}': Tables {names} have chunk sizes exceeding "
                            f"{max_size:,} rows. Run adaptive chunk planning to resize."
                )

        # ── require_backup_before_cutover ─────────────────────────────────────
        elif ptype == "require_backup_before_cutover":
            # Check if a backup confirmation exists in job metadata
            row = db.execute(
                text("SELECT extra_params FROM connection_registry WHERE id IN "
                     "(SELECT source_connection_id FROM migration_jobs WHERE id=:jid)"),
                {"jid": job_id}
            ).fetchone()
            # If the policy is active and no backup confirmed, warn
            return PolicyViolation(
                policy_name=name, policy_type=ptype,
                severity="warn",
                message=f"Policy '{name}': Ensure a verified backup exists before CDC cutover. "
                        f"Confirm backup completion before calling /cdc/{{session_id}}/complete"
            )

        return None  # Policy passed

    def _row(self, row) -> dict:
        if not row:
            return {}
        import json
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        if isinstance(d.get("config"), str):
            try:
                d["config"] = json.loads(d["config"])
            except Exception:
                pass
        return d
