"""
Validator Plugins
File: migration/backend/plugins/validators/validator_plugins.py

Refactors the existing Validation Engine into kernel plugins.
Every validator implements ValidatorPlugin and registers with PluginManager.

Why this matters:
    Before: validation logic was hardcoded in the Validation Engine service.
    After:  each validator is an independent plugin. New validators can be
            added without touching core code. Validators from the Marketplace
            (Part 14) install and register the same way as built-ins.

Built-in validators (matching existing validation_engine/validator.py):
    row_count_validator     → source and target row counts match
    checksum_validator      → MD5 checksum of all data in range matches
    sample_validator        → random sample of rows match field-by-field
    null_check_validator    → no unexpected NULLs in required columns
    business_rule_validator → custom SQL WHERE clause that must return 0 rows

Each validator runs as a WorkflowNode via ValidationPluginNode (below)
or is called directly from the existing schema_mapping_service validation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


# ── Base class ────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:       bool
    validator:    str
    table_name:   str
    message:      str
    details:      Dict[str, Any] = field(default_factory=dict)
    severity:     str = "error"   # error | warning | info

    def to_dict(self) -> dict:
        return {
            "passed":     self.passed,
            "validator":  self.validator,
            "table_name": self.table_name,
            "message":    self.message,
            "details":    self.details,
            "severity":   self.severity,
        }


class ValidatorPlugin(ABC):
    """
    Base class for all validator plugins.
    Validators are stateless — instantiated fresh per call.
    config is passed at instantiation from the workflow node or API.
    """

    name:         str = "base_validator"
    display_name: str = "Base Validator"
    description:  str = ""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def validate(
        self,
        source_connector,
        target_connector,
        table_name: str,
        pk_column:  str,
        pk_start:   Any,
        pk_end:     Any,
    ) -> ValidationResult:
        """
        Run the validation check for a chunk (pk_start → pk_end).
        Returns ValidationResult with passed=True/False.
        """


# ── Built-in validators ───────────────────────────────────────────────────────

class RowCountValidator(ValidatorPlugin):
    """
    Verifies that the number of rows in the PK range matches
    between source and target. The most fundamental check.
    """
    name         = "row_count_validator"
    display_name = "Row Count Validator"
    description  = "Verifies row counts match between source and target for a chunk."

    def validate(self, source_connector, target_connector,
                 table_name, pk_column, pk_start, pk_end) -> ValidationResult:
        try:
            src_count = source_connector.count_rows_in_range(
                table_name, pk_column, pk_start, pk_end
            )
            tgt_table = self.config.get("target_table_name", table_name)
            tgt_count = target_connector.count_rows_in_range(
                tgt_table, pk_column, pk_start, pk_end
            )

            passed  = src_count == tgt_count
            details = {"source_count": src_count, "target_count": tgt_count,
                       "diff": abs(src_count - tgt_count)}

            return ValidationResult(
                passed=passed,
                validator=self.name,
                table_name=table_name,
                message=(
                    f"Row counts match: {src_count:,}" if passed else
                    f"Row count mismatch: source={src_count:,}, target={tgt_count:,}, "
                    f"diff={details['diff']:,}"
                ),
                details=details,
            )
        except Exception as e:
            return ValidationResult(
                passed=False, validator=self.name, table_name=table_name,
                message=f"Row count validation failed: {e}", details={"error": str(e)}
            )


class ChecksumValidator(ValidatorPlugin):
    """
    Computes MD5 checksum of all column values in the range on both
    source and target. More thorough than row count alone.
    """
    name         = "checksum_validator"
    display_name = "Checksum Validator"
    description  = "MD5 checksum comparison of all row data in a chunk."

    def validate(self, source_connector, target_connector,
                 table_name, pk_column, pk_start, pk_end) -> ValidationResult:
        try:
            src_checksum = source_connector.compute_checksum(
                table_name, pk_column, pk_start, pk_end
            )
            tgt_table    = self.config.get("target_table_name", table_name)
            tgt_checksum = target_connector.compute_checksum(
                tgt_table, pk_column, pk_start, pk_end
            )

            passed = src_checksum == tgt_checksum
            return ValidationResult(
                passed=passed,
                validator=self.name,
                table_name=table_name,
                message=(
                    "Checksums match" if passed else
                    f"Checksum mismatch: source={src_checksum}, target={tgt_checksum}"
                ),
                details={"source_checksum": src_checksum, "target_checksum": tgt_checksum},
            )
        except Exception as e:
            return ValidationResult(
                passed=False, validator=self.name, table_name=table_name,
                message=f"Checksum validation failed: {e}", details={"error": str(e)}
            )


class SampleValidator(ValidatorPlugin):
    """
    Fetches a random sample of rows from source and target and
    compares them field-by-field. Catches data transformation errors
    that checksums might miss if column order changes.

    config: {"sample_size": 100, "columns": ["col1", "col2"]}
    """
    name         = "sample_validator"
    display_name = "Sample Row Validator"
    description  = "Compares a random sample of rows field-by-field."

    def validate(self, source_connector, target_connector,
                 table_name, pk_column, pk_start, pk_end) -> ValidationResult:
        try:
            sample_size = self.config.get("sample_size", 100)
            tgt_table   = self.config.get("target_table_name", table_name)
            columns     = self.config.get("columns")

            src_rows = list(source_connector.stream_rows(
                table_name, pk_column, pk_start, pk_end,
                columns=columns, batch_size=sample_size
            ))[:sample_size]

            tgt_rows = list(target_connector.stream_rows(
                tgt_table, pk_column, pk_start, pk_end,
                columns=columns, batch_size=sample_size
            ))[:sample_size]

            if len(src_rows) != len(tgt_rows):
                return ValidationResult(
                    passed=False, validator=self.name, table_name=table_name,
                    message=f"Sample size mismatch: source={len(src_rows)}, target={len(tgt_rows)}",
                    details={"source_sample": len(src_rows), "target_sample": len(tgt_rows)},
                )

            mismatches = []
            for i, (sr, tr) in enumerate(zip(src_rows, tgt_rows)):
                for key in sr:
                    if str(sr.get(key)) != str(tr.get(key)):
                        mismatches.append({
                            "row_index": i,
                            "column":    key,
                            "source":    str(sr.get(key))[:50],
                            "target":    str(tr.get(key))[:50],
                        })
                        if len(mismatches) >= 5:
                            break
                if len(mismatches) >= 5:
                    break

            passed = len(mismatches) == 0
            return ValidationResult(
                passed=passed,
                validator=self.name,
                table_name=table_name,
                message=(
                    f"Sample validation passed ({len(src_rows)} rows checked)" if passed else
                    f"Sample mismatch: {len(mismatches)} difference(s) found in {len(src_rows)} rows checked"
                ),
                details={"rows_sampled": len(src_rows), "mismatches": mismatches},
            )
        except Exception as e:
            return ValidationResult(
                passed=False, validator=self.name, table_name=table_name,
                message=f"Sample validation failed: {e}", details={"error": str(e)}
            )


class NullCheckValidator(ValidatorPlugin):
    """
    Checks that required columns (NOT NULL in target schema) have no
    NULL values in the migrated data.

    config: {"required_columns": ["id", "email", "created_at"]}
    """
    name         = "null_check_validator"
    display_name = "NULL Check Validator"
    description  = "Verifies no NULLs in required columns on target."

    def validate(self, source_connector, target_connector,
                 table_name, pk_column, pk_start, pk_end) -> ValidationResult:
        required_columns = self.config.get("required_columns", [pk_column])
        tgt_table        = self.config.get("target_table_name", table_name)

        violations = []
        for col in required_columns:
            try:
                conn   = target_connector._connection
                engine = getattr(target_connector, "name", "mysql")
                q      = (lambda c: f"`{c}`") if engine == "mysql" else (lambda c: f'"{c}"')
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT COUNT(*) FROM {q(tgt_table)} "
                    f"WHERE {q(pk_column)} BETWEEN %s AND %s "
                    f"AND {q(col)} IS NULL",
                    (pk_start, pk_end)
                )
                null_count = cursor.fetchone()[0] or 0
                cursor.close()
                if null_count > 0:
                    violations.append({"column": col, "null_count": null_count})
            except Exception as e:
                violations.append({"column": col, "error": str(e)})

        passed = len(violations) == 0
        return ValidationResult(
            passed=passed,
            validator=self.name,
            table_name=table_name,
            message=(
                "No NULL violations found" if passed else
                f"NULL violations in {len(violations)} column(s)"
            ),
            details={"violations": violations},
        )


class BusinessRuleValidator(ValidatorPlugin):
    """
    Runs a custom SQL expression against the target table.
    The SQL must return 0 rows to pass.

    config: {"sql": "SELECT * FROM {table} WHERE amount < 0 AND {pk} BETWEEN {start} AND {end}",
             "description": "No negative amounts"}
    """
    name         = "business_rule_validator"
    display_name = "Business Rule Validator"
    description  = "Custom SQL rule that must return 0 rows to pass."

    def validate(self, source_connector, target_connector,
                 table_name, pk_column, pk_start, pk_end) -> ValidationResult:
        sql_template = self.config.get("sql", "")
        description  = self.config.get("description", "Business rule check")
        tgt_table    = self.config.get("target_table_name", table_name)

        if not sql_template:
            return ValidationResult(
                passed=True, validator=self.name, table_name=table_name,
                message="No SQL rule configured", severity="info",
            )

        try:
            sql = sql_template.format(
                table=tgt_table, pk=pk_column,
                start=pk_start, end=pk_end
            )
            conn   = target_connector._connection
            cursor = conn.cursor()
            cursor.execute(sql)
            violations = cursor.fetchall()
            cursor.close()

            passed = len(violations) == 0
            return ValidationResult(
                passed=passed,
                validator=self.name,
                table_name=table_name,
                message=(
                    f"Business rule passed: {description}" if passed else
                    f"Business rule FAILED: {description} — {len(violations)} violation(s)"
                ),
                details={
                    "rule":       description,
                    "violations": len(violations),
                    "samples":    [str(r)[:100] for r in violations[:3]],
                },
            )
        except Exception as e:
            return ValidationResult(
                passed=False, validator=self.name, table_name=table_name,
                message=f"Business rule validation error: {e}",
                details={"error": str(e), "sql": sql_template},
            )


# ── Registration helper ───────────────────────────────────────────────────────

def register_all_validators():
    """Register all built-in validators with the PluginManager."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        for cls in [RowCountValidator, ChecksumValidator, SampleValidator,
                    NullCheckValidator, BusinessRuleValidator]:
            PluginManager.register(
                plugin_type=PluginType.VALIDATOR,
                name=cls.name,
                plugin_class=cls,
                display_name=cls.display_name,
                is_builtin=True,
            )
        from backend.shared.config.logging import logger
        logger.info("Validator plugins registered", count=5)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to register validators: {e}")
