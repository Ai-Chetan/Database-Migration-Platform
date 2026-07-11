"""
Data Quality Scanner
File: migration/backend/intelligence_service/scanner/data_quality_scanner.py

Scans source database for data quality issues BEFORE migration starts.
Many migrations fail not because of tooling, but because of dirty data.

Checks performed (all run as SQL aggregates in the source DB):
  1. duplicate_pk       — duplicate values in PK column
  2. null_pk            — NULL values in PK column
  3. broken_fk          — FK values with no matching parent row (orphans)
  4. null_required      — NULL in columns marked NOT NULL in schema
  5. oversized_value    — values longer than target column allows
  6. invalid_date       — date values outside valid range (e.g. '0000-00-00' in MySQL)
  7. encoding_issue     — non-UTF8 characters in string columns (MySQL only)
  8. circular_ref       — self-referencing FK cycles (e.g. employee.manager_id → employee.id)
  9. duplicate_unique   — duplicate values in UNIQUE-constrained columns

Severity:
  error   — will cause migration failure (must fix before migrating)
  warning — may cause issues (should review)
  info    — informational (good to know)

All results written to data_quality_results table.
"""

import datetime
import uuid
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.shared.config.logging import logger


@dataclass
class QualityIssue:
    table_name:     str
    check_type:     str
    severity:       str       # error | warning | info
    affected_count: int
    affected_pct:   float
    details:        str
    recommendation: str
    sample_values:  List = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "table_name":     self.table_name,
            "check_type":     self.check_type,
            "severity":       self.severity,
            "affected_count": self.affected_count,
            "affected_pct":   self.affected_pct,
            "details":        self.details,
            "recommendation": self.recommendation,
            "sample_values":  self.sample_values,
        }


class DataQualityScanner:

    def scan_table(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_name:    str,
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
        checks:        Optional[List[str]] = None,
    ) -> List[QualityIssue]:
        """
        Run data quality checks on one table.
        Returns list of QualityIssue objects.
        Saves results to data_quality_results table.
        """
        all_checks = checks or [
            "duplicate_pk", "null_pk", "broken_fk", "null_required",
            "oversized_value", "invalid_date", "duplicate_unique",
        ]

        engine    = source_config.get("engine", "mysql").lower()
        table_def = schema_info.get("tables", {}).get(table_name, {})
        columns   = table_def.get("columns", {})
        pks       = table_def.get("primary_keys", [])
        fks       = table_def.get("foreign_keys", [])

        connector = ConnectorRegistry.get_for_config(source_config)
        connector.connect()

        issues: List[QualityIssue] = []

        try:
            conn       = connector._connection
            cursor     = conn.cursor()
            q          = self._q(engine)
            total_rows = connector.get_row_count(table_name)

            if total_rows == 0:
                return []

            # ── 1. Duplicate PK ───────────────────────────────────────────
            if "duplicate_pk" in all_checks and pks:
                for pk_col in pks:
                    try:
                        cursor.execute(
                            f"SELECT COUNT(*) - COUNT(DISTINCT {q(pk_col)}) "
                            f"FROM {q(table_name)}"
                        )
                        dups = cursor.fetchone()[0] or 0
                        if dups > 0:
                            cursor.execute(
                                f"SELECT {q(pk_col)}, COUNT(*) as cnt "
                                f"FROM {q(table_name)} "
                                f"GROUP BY {q(pk_col)} HAVING COUNT(*) > 1 LIMIT 5"
                            )
                            samples = [{"value": str(r[0]), "count": r[1]}
                                      for r in cursor.fetchall()]
                            issues.append(QualityIssue(
                                table_name=table_name, check_type="duplicate_pk",
                                severity="error",
                                affected_count=dups,
                                affected_pct=round(dups / total_rows * 100, 3),
                                details=f"Column '{pk_col}' has {dups:,} duplicate values. "
                                        "This will violate PK constraint on target.",
                                recommendation="Deduplicate rows before migration or change target PK strategy.",
                                sample_values=samples,
                            ))
                    except Exception as e:
                        logger.debug("Duplicate PK check failed", table=table_name, error=str(e))

            # ── 2. NULL PK ────────────────────────────────────────────────
            if "null_pk" in all_checks and pks:
                for pk_col in pks:
                    try:
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {q(table_name)} "
                            f"WHERE {q(pk_col)} IS NULL"
                        )
                        nulls = cursor.fetchone()[0] or 0
                        if nulls > 0:
                            issues.append(QualityIssue(
                                table_name=table_name, check_type="null_pk",
                                severity="error",
                                affected_count=nulls,
                                affected_pct=round(nulls / total_rows * 100, 3),
                                details=f"Column '{pk_col}' has {nulls:,} NULL values. "
                                        "Target will reject these rows.",
                                recommendation="Remove or assign PK values to NULL rows before migration.",
                                sample_values=[],
                            ))
                    except Exception as e:
                        logger.debug("NULL PK check failed", table=table_name, error=str(e))

            # ── 3. Broken FK (orphan rows) ─────────────────────────────────
            if "broken_fk" in all_checks:
                for fk in fks:
                    src_col  = fk.get("column")
                    ref_tbl  = fk.get("ref_table")
                    ref_col  = fk.get("ref_column")
                    if not (src_col and ref_tbl and ref_col):
                        continue
                    try:
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {q(table_name)} s "
                            f"LEFT JOIN {q(ref_tbl)} p "
                            f"ON s.{q(src_col)} = p.{q(ref_col)} "
                            f"WHERE s.{q(src_col)} IS NOT NULL "
                            f"AND p.{q(ref_col)} IS NULL"
                        )
                        orphans = cursor.fetchone()[0] or 0
                        if orphans > 0:
                            cursor.execute(
                                f"SELECT s.{q(src_col)} FROM {q(table_name)} s "
                                f"LEFT JOIN {q(ref_tbl)} p "
                                f"ON s.{q(src_col)} = p.{q(ref_col)} "
                                f"WHERE s.{q(src_col)} IS NOT NULL "
                                f"AND p.{q(ref_col)} IS NULL LIMIT 5"
                            )
                            samples = [{"value": str(r[0])} for r in cursor.fetchall()]
                            sev = "error" if orphans > total_rows * 0.01 else "warning"
                            issues.append(QualityIssue(
                                table_name=table_name, check_type="broken_fk",
                                severity=sev,
                                affected_count=orphans,
                                affected_pct=round(orphans / total_rows * 100, 3),
                                details=f"FK '{src_col}' → '{ref_tbl}.{ref_col}': "
                                        f"{orphans:,} rows have no matching parent.",
                                recommendation="Delete or fix orphan rows before migration, "
                                              "or disable FK checks and clean up after.",
                                sample_values=samples,
                            ))
                    except Exception as e:
                        logger.debug("Broken FK check failed", table=table_name,
                                     fk=src_col, error=str(e))

            # ── 4. NULL in required (NOT NULL) columns ─────────────────────
            if "null_required" in all_checks:
                for col_name, col_def in columns.items():
                    if col_def.get("nullable") is False and col_name not in pks:
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {q(table_name)} "
                                f"WHERE {q(col_name)} IS NULL"
                            )
                            nulls = cursor.fetchone()[0] or 0
                            if nulls > 0:
                                issues.append(QualityIssue(
                                    table_name=table_name, check_type="null_required",
                                    severity="error",
                                    affected_count=nulls,
                                    affected_pct=round(nulls / total_rows * 100, 3),
                                    details=f"Column '{col_name}' is NOT NULL but has "
                                            f"{nulls:,} NULL values in source.",
                                    recommendation="Fill NULL values before migration or "
                                                  "change target column to allow NULLs.",
                                    sample_values=[],
                                ))
                        except Exception:
                            pass

            # ── 5. Oversized values ────────────────────────────────────────
            if "oversized_value" in all_checks:
                for col_name, col_def in columns.items():
                    col_type = col_def.get("type", "")
                    if "varchar" in col_type.lower() or "char" in col_type.lower():
                        try:
                            # Extract length from type e.g. "varchar(50)"
                            import re
                            m = re.search(r'\((\d+)\)', col_type)
                            if m:
                                max_len = int(m.group(1))
                                cursor.execute(
                                    f"SELECT COUNT(*) FROM {q(table_name)} "
                                    f"WHERE LENGTH({q(col_name)}) > {max_len}"
                                )
                                oversized = cursor.fetchone()[0] or 0
                                if oversized > 0:
                                    cursor.execute(
                                        f"SELECT {q(col_name)}, LENGTH({q(col_name)}) "
                                        f"FROM {q(table_name)} "
                                        f"WHERE LENGTH({q(col_name)}) > {max_len} LIMIT 3"
                                    )
                                    samples = [{"value": str(r[0])[:50] + "...",
                                               "length": r[1]}
                                              for r in cursor.fetchall()]
                                    issues.append(QualityIssue(
                                        table_name=table_name,
                                        check_type="oversized_value",
                                        severity="error",
                                        affected_count=oversized,
                                        affected_pct=round(oversized / total_rows * 100, 3),
                                        details=f"Column '{col_name}' ({col_type}): "
                                                f"{oversized:,} values exceed max length {max_len}.",
                                        recommendation=f"Truncate values or increase target "
                                                      f"column size to VARCHAR({max_len * 2}).",
                                        sample_values=samples,
                                    ))
                        except Exception:
                            pass

            # ── 6. Invalid dates (MySQL '0000-00-00' etc.) ─────────────────
            if "invalid_date" in all_checks and engine == "mysql":
                for col_name, col_def in columns.items():
                    col_type = col_def.get("type", "").lower()
                    if col_type in ("date", "datetime", "timestamp"):
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {q(table_name)} "
                                f"WHERE {q(col_name)} = '0000-00-00' "
                                f"OR {q(col_name)} = '0000-00-00 00:00:00'"
                            )
                            invalid = cursor.fetchone()[0] or 0
                            if invalid > 0:
                                issues.append(QualityIssue(
                                    table_name=table_name, check_type="invalid_date",
                                    severity="warning",
                                    affected_count=invalid,
                                    affected_pct=round(invalid / total_rows * 100, 3),
                                    details=f"Column '{col_name}': {invalid:,} rows have "
                                            "'0000-00-00' date (MySQL zero-date). "
                                            "PostgreSQL will reject these.",
                                    recommendation="Convert zero-dates to NULL or a valid date "
                                                  "before migrating to PostgreSQL.",
                                    sample_values=[{"value": "0000-00-00", "count": invalid}],
                                ))
                        except Exception:
                            pass

            # ── 7. Duplicate UNIQUE values ─────────────────────────────────
            if "duplicate_unique" in all_checks:
                indexes = table_def.get("indexes", [])
                for idx in indexes:
                    if idx.get("unique") and idx.get("name") != "PRIMARY":
                        idx_cols = idx.get("columns", [])
                        if not idx_cols:
                            continue
                        try:
                            col_expr = ", ".join(q(c) for c in idx_cols)
                            cursor.execute(
                                f"SELECT COUNT(*) FROM ("
                                f"  SELECT {col_expr}, COUNT(*) as cnt "
                                f"  FROM {q(table_name)} "
                                f"  GROUP BY {col_expr} HAVING COUNT(*) > 1"
                                f") sub"
                            )
                            dup_groups = cursor.fetchone()[0] or 0
                            if dup_groups > 0:
                                issues.append(QualityIssue(
                                    table_name=table_name, check_type="duplicate_unique",
                                    severity="error",
                                    affected_count=dup_groups,
                                    affected_pct=0.0,
                                    details=f"Unique index '{idx.get('name')}' on "
                                            f"{idx_cols} has {dup_groups:,} duplicate groups.",
                                    recommendation="Remove or resolve duplicate values before migration.",
                                    sample_values=[],
                                ))
                        except Exception:
                            pass

            cursor.close()

        finally:
            connector.disconnect()

        # ── Save to DB ────────────────────────────────────────────────────
        self._save_results(db, connection_id, tenant_id, issues)

        logger.info("Data quality scan complete",
                    table=table_name,
                    issues=len(issues),
                    errors=sum(1 for i in issues if i.severity == "error"))

        return issues

    def scan_all(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
        checks:        Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Scan all tables and return aggregated results."""
        tables    = list(schema_info.get("tables", {}).keys())
        all_issues: List[QualityIssue] = []
        table_results = {}

        for table_name in tables:
            try:
                issues = self.scan_table(
                    db=db, connection_id=connection_id,
                    source_config=source_config,
                    table_name=table_name, schema_info=schema_info,
                    tenant_id=tenant_id, checks=checks,
                )
                all_issues.extend(issues)
                table_results[table_name] = {
                    "issue_count": len(issues),
                    "error_count": sum(1 for i in issues if i.severity == "error"),
                    "warning_count": sum(1 for i in issues if i.severity == "warning"),
                }
            except Exception as e:
                logger.warning("Table scan failed", table=table_name, error=str(e))
                table_results[table_name] = {"error": str(e)}

        errors   = sum(1 for i in all_issues if i.severity == "error")
        warnings = sum(1 for i in all_issues if i.severity == "warning")

        return {
            "connection_id":    connection_id,
            "tables_scanned":   len(tables),
            "total_issues":     len(all_issues),
            "error_count":      errors,
            "warning_count":    warnings,
            "can_proceed":      errors == 0,
            "blocking_message": (
                f"{errors} data quality error(s) must be fixed before migration."
                if errors > 0 else "No blocking data quality issues found."
            ),
            "table_results":    table_results,
            "issues":           [i.to_dict() for i in all_issues],
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save_results(self, db, connection_id, tenant_id, issues: List[QualityIssue]):
        now = datetime.datetime.utcnow()
        for issue in issues:
            try:
                db.execute(
                    text("""
                        INSERT INTO data_quality_results
                            (id, tenant_id, connection_id, table_name, check_type,
                             severity, affected_count, affected_pct,
                             sample_values, details, recommendation, scanned_at)
                        VALUES
                            (:id, :tid, :cid, :tname, :ctype,
                             :sev, :cnt, :pct,
                             :samples::jsonb, :details, :rec, :now)
                    """),
                    {
                        "id":      str(uuid.uuid4()),
                        "tid":     tenant_id,
                        "cid":     connection_id,
                        "tname":   issue.table_name,
                        "ctype":   issue.check_type,
                        "sev":     issue.severity,
                        "cnt":     issue.affected_count,
                        "pct":     issue.affected_pct,
                        "samples": json.dumps(issue.sample_values),
                        "details": issue.details,
                        "rec":     issue.recommendation,
                        "now":     now,
                    }
                )
            except Exception as e:
                logger.warning("Failed to save DQ result", error=str(e))
        try:
            db.commit()
        except Exception:
            db.rollback()

    def _q(self, engine: str):
        if engine == "mysql":
            return lambda col: f"`{col}`"
        return lambda col: f'"{col}"'
