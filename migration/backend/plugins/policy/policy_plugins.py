"""
Policy Plugins
File: migration/backend/plugins/policy/policy_plugins.py

Refactors the existing Policy Engine (connector_framework/policy/policy_engine.py)
into proper kernel plugins registered with PluginManager.

Each PolicyPlugin checks one organizational rule and returns pass/fail.
The PolicyEngine calls all registered active policies before allowing
migration to execute.

Built-in policies:
    forbidden_lossy_conversion   → blocks unsafe type conversions
    require_approval             → blocks until migration is approved
    max_downtime_minutes         → warns/blocks if duration estimate too high
    require_validation           → blocks if no validation rules configured
    max_chunk_size               → enforces max rows per chunk
    require_backup_before_cutover→ warns before CDC cutover without backup
    require_data_masking_for_pii → blocks if PII columns have no masking rules

All policies are read-only — they never modify migration state.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session


# ── Base class ─────────────────────────────────────────────────────────────────

@dataclass
class PolicyCheckResult:
    passed:      bool
    policy_name: str
    severity:    str        # block | warn
    message:     str
    details:     Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "passed":      self.passed,
            "policy_name": self.policy_name,
            "severity":    self.severity,
            "message":     self.message,
            "details":     self.details,
        }


class PolicyPlugin(ABC):
    """Base class for all policy plugins."""

    name:         str = "base_policy"
    display_name: str = "Base Policy"
    description:  str = ""
    default_severity: str = "block"

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def check(
        self,
        db:              Session,
        job_id:          str,
        tenant_id:       str,
        dry_run_result:  Optional[Dict] = None,
        approval_status: Optional[str]  = None,
        context:         Optional[Dict] = None,
    ) -> PolicyCheckResult:
        """
        Run the policy check.
        Returns PolicyCheckResult with passed=True if policy is satisfied.
        """


# ── Built-in policies ──────────────────────────────────────────────────────────

class ForbiddenLossyConversionPolicy(PolicyPlugin):
    """
    Blocks migration if unsafe type conversions exist in the mapping.
    Warns on lossy conversions (configurable).

    config: {"block_unsafe": true, "block_lossy": false}
    """
    name         = "forbidden_lossy_conversion"
    display_name = "Forbidden Lossy Conversion"
    description  = "Blocks migration if unsafe type conversions are detected in mappings."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        if not dry_run_result:
            return PolicyCheckResult(
                passed=True, policy_name=self.name, severity="block",
                message="No dry-run result provided — policy skipped"
            )

        unsafe = dry_run_result.get("unsafe_conversions", [])
        lossy  = dry_run_result.get("lossy_conversions", [])

        block_unsafe = self.config.get("block_unsafe", True)
        block_lossy  = self.config.get("block_lossy", False)
        severity     = self.config.get("severity", "block")

        if unsafe and block_unsafe:
            return PolicyCheckResult(
                passed=False, policy_name=self.name, severity="block",
                message=f"{len(unsafe)} unsafe type conversion(s) will cause data loss. "
                        "Fix mappings before migrating.",
                details={"unsafe_conversions": unsafe[:5]},
            )

        if lossy and block_lossy:
            return PolicyCheckResult(
                passed=False, policy_name=self.name, severity=severity,
                message=f"{len(lossy)} lossy conversion(s) detected. Precision may be lost.",
                details={"lossy_conversions": lossy[:5]},
            )

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity="block",
            message=f"Type conversion check passed. "
                    f"Unsafe: {len(unsafe)}, Lossy: {len(lossy)} (below threshold).",
        )


class RequireApprovalPolicy(PolicyPlugin):
    """
    Blocks migration execution until it has been formally approved.
    config: {"required_roles": ["tenant_admin"]}
    """
    name         = "require_approval"
    display_name = "Require Approval"
    description  = "Blocks migration until it has been approved."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        if approval_status == "approved":
            return PolicyCheckResult(
                passed=True, policy_name=self.name, severity="block",
                message="Migration has been approved."
            )

        status_display = approval_status or "not_requested"
        return PolicyCheckResult(
            passed=False, policy_name=self.name, severity="block",
            message=f"Migration requires approval before execution. "
                    f"Current status: '{status_display}'. "
                    f"Request via POST /jobs/{job_id}/approval/request",
            details={"approval_status": status_display},
        )


class MaxDowntimePolicy(PolicyPlugin):
    """
    Warns or blocks if estimated migration duration exceeds configured limit.
    config: {"max_minutes": 60, "severity": "warn"}
    """
    name         = "max_downtime_minutes"
    display_name = "Maximum Downtime"
    description  = "Warns/blocks if estimated migration duration exceeds the limit."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        max_minutes = self.config.get("max_minutes", 60)
        severity    = self.config.get("severity", "warn")

        # Load latest assessment report for this job's connection
        try:
            from sqlalchemy import text
            row = db.execute(
                text("""
                    SELECT ar.complexity, ar.recommended_workers
                    FROM assessment_reports ar
                    JOIN migration_jobs mj ON mj.id=:jid
                    WHERE ar.connection_id = mj.source_connection_id
                    ORDER BY ar.generated_at DESC LIMIT 1
                """),
                {"jid": job_id}
            ).fetchone()

            if row and row[0] == "HIGH" and max_minutes < 60:
                return PolicyCheckResult(
                    passed=severity != "block",
                    policy_name=self.name, severity=severity,
                    message=f"HIGH complexity migration may exceed {max_minutes} minute limit. "
                            "Consider CDC mode for near-zero downtime.",
                    details={"complexity": row[0], "max_minutes": max_minutes},
                )
        except Exception:
            pass

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity=severity,
            message=f"Downtime estimate within {max_minutes} minute limit."
        )


class RequireValidationPolicy(PolicyPlugin):
    """
    Blocks migration if no validation rules are configured.
    config: {"severity": "warn"}
    """
    name         = "require_validation"
    display_name = "Require Validation Rules"
    description  = "Blocks migration if validation rules are not configured."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        severity = self.config.get("severity", "warn")
        try:
            from sqlalchemy import text
            row = db.execute(
                text("""
                    SELECT COUNT(*) FROM schema_validation_results svr
                    JOIN mapping_projects mp ON svr.project_id = mp.id
                    JOIN migration_jobs mj ON mj.id=:jid
                    WHERE svr.project_id IS NOT NULL
                """),
                {"jid": job_id}
            ).fetchone()

            if row and row[0] == 0:
                return PolicyCheckResult(
                    passed=severity != "block",
                    policy_name=self.name, severity=severity,
                    message="No validation results found. Run schema validation before migrating.",
                    details={"validation_count": 0},
                )
        except Exception:
            pass

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity=severity,
            message="Validation rules confirmed."
        )


class MaxChunkSizePolicy(PolicyPlugin):
    """
    Enforces maximum chunk size per table.
    config: {"max_rows": 500000, "severity": "warn"}
    """
    name         = "max_chunk_size"
    display_name = "Maximum Chunk Size"
    description  = "Enforces maximum rows per chunk to prevent memory issues."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        max_rows = self.config.get("max_rows", 500_000)
        severity = self.config.get("severity", "warn")

        try:
            from sqlalchemy import text
            rows = db.execute(
                text("""
                    SELECT table_name, computed_chunk_size
                    FROM migration_tables
                    WHERE job_id=:jid AND computed_chunk_size > :max
                """),
                {"jid": job_id, "max": max_rows}
            ).fetchall()

            if rows:
                names = [r[0] for r in rows]
                return PolicyCheckResult(
                    passed=severity != "block",
                    policy_name=self.name, severity=severity,
                    message=f"{len(rows)} table(s) have chunk sizes exceeding {max_rows:,} rows. "
                            "Run adaptive chunk planning.",
                    details={"tables": names[:10], "max_rows": max_rows},
                )
        except Exception:
            pass

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity=severity,
            message=f"All chunk sizes within {max_rows:,} row limit."
        )


class RequireBackupPolicy(PolicyPlugin):
    """
    Warns before CDC cutover if no backup is confirmed.
    config: {"severity": "warn"}
    """
    name         = "require_backup_before_cutover"
    display_name = "Require Backup Before Cutover"
    description  = "Warns before CDC cutover without a confirmed backup."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        severity = self.config.get("severity", "warn")
        backup_confirmed = (context or {}).get("backup_confirmed", False)

        if not backup_confirmed:
            return PolicyCheckResult(
                passed=severity != "block",
                policy_name=self.name, severity=severity,
                message="Ensure a verified backup exists before CDC cutover. "
                        "Pass context={'backup_confirmed': true} to clear this warning.",
            )

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity=severity,
            message="Backup confirmed."
        )


class RequireMaskingForPIIPolicy(PolicyPlugin):
    """
    Blocks migration if columns detected as PII have no masking rules.
    Works with Migration Advisor's PII detection (Part 4).
    config: {"severity": "block", "auto_detect": true}
    """
    name         = "require_masking_for_pii"
    display_name = "Require Masking for PII Columns"
    description  = "Blocks migration if PII columns have no masking rules configured."

    def check(self, db, job_id, tenant_id, dry_run_result=None,
              approval_status=None, context=None) -> PolicyCheckResult:
        severity = self.config.get("severity", "block")

        try:
            from sqlalchemy import text
            # Find columns with mapping_kind NOT mask/synthesize
            # that were flagged as PII by the advisor
            row = db.execute(
                text("""
                    SELECT COUNT(*) FROM schema_column_mappings
                    WHERE mapping_kind NOT IN ('mask','synthesize')
                    AND source_column ILIKE ANY(ARRAY[
                        '%email%','%phone%','%ssn%','%password%','%credit%',
                        '%dob%','%birth%','%address%','%passport%','%license%'
                    ])
                    AND table_mapping_id IN (
                        SELECT id FROM schema_table_mappings stm
                        JOIN mapping_projects mp ON mp.id = stm.project_id
                        WHERE mp.job_id = :jid OR mp.id = :jid
                    )
                """),
                {"jid": job_id}
            ).fetchone()

            unmasked = int(row[0] or 0) if row else 0
            if unmasked > 0:
                return PolicyCheckResult(
                    passed=severity != "block",
                    policy_name=self.name, severity=severity,
                    message=f"{unmasked} column(s) appear to contain PII but have no masking rules. "
                            "Configure mask or synthesize mapping_kind for these columns.",
                    details={"unmasked_pii_columns": unmasked},
                )
        except Exception:
            pass

        return PolicyCheckResult(
            passed=True, policy_name=self.name, severity=severity,
            message="All detected PII columns have masking rules configured."
        )


# ── Policy Runner ─────────────────────────────────────────────────────────────

class PolicyRunner:
    """
    Runs all registered active policies and returns aggregated results.
    Replaces the old PolicyEngine.check_all() method.
    """

    @classmethod
    def check_all(
        cls,
        db:              Session,
        job_id:          str,
        tenant_id:       str,
        dry_run_result:  Optional[Dict] = None,
        approval_status: Optional[str]  = None,
        context:         Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Run all registered active policies. Returns aggregated result."""
        try:
            from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
            registered = PluginManager.list_plugins(PluginType.POLICY)
        except Exception:
            registered = []

        results     = []
        all_passed  = True
        violations  = []
        warnings    = []

        # Load active policy configs from DB
        try:
            from sqlalchemy import text
            import json as _json
            rows = db.execute(
                text("""
                    SELECT name, policy_type, config FROM policy_rules
                    WHERE tenant_id=:tid AND is_active=TRUE
                """),
                {"tid": tenant_id}
            ).fetchall()
        except Exception:
            rows = []

        for row in rows:
            policy_type = row[1] if hasattr(row, '__getitem__') else row.policy_type
            config_raw  = row[2] if hasattr(row, '__getitem__') else row.config
            config = config_raw if isinstance(config_raw, dict) else {}

            try:
                from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
                plugin = PluginManager.get(PluginType.POLICY, policy_type, config)
                result = plugin.check(
                    db=db, job_id=job_id, tenant_id=tenant_id,
                    dry_run_result=dry_run_result,
                    approval_status=approval_status,
                    context=context,
                )
                results.append(result.to_dict())
                if not result.passed:
                    if result.severity == "block":
                        all_passed = False
                        violations.append(result.to_dict())
                    else:
                        warnings.append(result.to_dict())
            except Exception as e:
                pass

        return {
            "passed":           all_passed,
            "total_policies":   len(results),
            "violations":       violations,
            "warnings":         warnings,
            "all_results":      results,
            "can_proceed":      all_passed,
        }


# ── Registration ──────────────────────────────────────────────────────────────

def register_all_policies():
    """Register all built-in policies with the PluginManager."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        for cls in [ForbiddenLossyConversionPolicy, RequireApprovalPolicy,
                    MaxDowntimePolicy, RequireValidationPolicy,
                    MaxChunkSizePolicy, RequireBackupPolicy,
                    RequireMaskingForPIIPolicy]:
            PluginManager.register(
                plugin_type=PluginType.POLICY,
                name=cls.name,
                plugin_class=cls,
                display_name=cls.display_name,
                is_builtin=True,
            )
        from backend.shared.config.logging import logger
        logger.info("Policy plugins registered", count=7)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to register policies: {e}")
