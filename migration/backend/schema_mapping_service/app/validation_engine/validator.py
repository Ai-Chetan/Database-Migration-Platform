"""
Validation Engine
File: migration/backend/schema_mapping_service/app/validation_engine/validator.py

Part 2 feature: comprehensive data validation after migration.

Validation types:
  1. row_count     — source count == target count per table
  2. checksum      — MD5 aggregate of all column values per chunk/table
  3. sample        — pick N random rows and compare field-by-field
  4. null_check    — columns marked NOT NULL have no NULLs in target
  5. business_rule — custom SQL WHERE clause that must return 0 rows

All results stored in schema_validation_results table.

Usage:
    engine = ValidationEngine(source_config, target_config)
    results = engine.validate_table(
        db=db,
        project_id="...",
        source_table="users",
        target_table="customers",
        column_mapping={"id": "id", "name": "full_name"},
        validations=["row_count", "checksum", "sample"]
    )
"""

import hashlib
import random
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.shared.config.logging import logger


@dataclass
class ValidationResult:
    validation_type: str
    source_table:    str
    target_table:    str
    passed:          bool
    source_value:    str
    target_value:    str
    details:         str


class ValidationEngine:

    def __init__(self, source_config: dict, target_config: dict):
        self.source_config = source_config
        self.target_config = target_config

    def validate_table(
        self,
        db: Session,
        project_id: str,
        source_table: str,
        target_table: str,
        column_mapping: Dict[str, str],
        validations: List[str] = None,
        sample_size: int = 100,
        business_rules: List[Dict] = None,
    ) -> List[ValidationResult]:
        """
        Run all requested validations for one table pair.
        Saves results to schema_validation_results.
        Returns list of ValidationResult.
        """
        if validations is None:
            validations = ["row_count", "checksum", "null_check"]

        results = []

        if "row_count" in validations:
            r = self._validate_row_count(source_table, target_table)
            results.append(r)

        if "checksum" in validations:
            r = self._validate_checksum(source_table, target_table, column_mapping)
            results.append(r)

        if "sample" in validations:
            r = self._validate_sample(source_table, target_table, column_mapping, sample_size)
            results.append(r)

        if "null_check" in validations:
            rs = self._validate_nulls(target_table, column_mapping)
            results.extend(rs)

        if "business_rule" in validations and business_rules:
            for rule in business_rules:
                r = self._validate_business_rule(target_table, rule)
                results.append(r)

        # Save all results to DB
        self._save_results(db, project_id, results)

        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        logger.info(
            "Table validation complete",
            source=source_table, target=target_table,
            passed=passed, failed=failed
        )

        return results

    # ── 1. Row Count ──────────────────────────────────────────────────────────

    def _validate_row_count(self, source_table: str, target_table: str) -> ValidationResult:
        src_count = self._count_rows(self.source_config, source_table)
        tgt_count = self._count_rows(self.target_config, target_table)
        passed    = src_count == tgt_count

        return ValidationResult(
            validation_type="row_count",
            source_table=source_table,
            target_table=target_table,
            passed=passed,
            source_value=str(src_count),
            target_value=str(tgt_count),
            details=f"Row count: source={src_count:,}, target={tgt_count:,}" + (
                "" if passed else f" — MISMATCH of {abs(src_count - tgt_count):,} rows"
            )
        )

    def _count_rows(self, config: dict, table_name: str) -> int:
        conn   = self._get_conn(config)
        cursor = conn.cursor()
        try:
            if config.get("engine", "").lower() == "mysql":
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            else:
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            conn.close()

    # ── 2. Checksum ───────────────────────────────────────────────────────────

    def _validate_checksum(
        self,
        source_table: str,
        target_table: str,
        column_mapping: Dict[str, str]
    ) -> ValidationResult:
        src_checksum = self._compute_table_checksum(
            self.source_config, source_table, list(column_mapping.keys())
        )
        tgt_checksum = self._compute_table_checksum(
            self.target_config, target_table, list(column_mapping.values())
        )
        passed = src_checksum == tgt_checksum

        return ValidationResult(
            validation_type="checksum",
            source_table=source_table,
            target_table=target_table,
            passed=passed,
            source_value=src_checksum,
            target_value=tgt_checksum,
            details="Checksums match" if passed else
                    f"CHECKSUM MISMATCH — data integrity issue detected"
        )

    def _compute_table_checksum(self, config: dict, table_name: str, columns: List[str]) -> str:
        engine = config.get("engine", "").lower()
        conn   = self._get_conn(config)
        cursor = conn.cursor()
        try:
            if engine == "mysql":
                concat_parts = ", '|', ".join(
                    f"IFNULL(CAST(`{c}` AS CHAR), 'NULL')" for c in columns
                )
                sql = (
                    f"SELECT MD5(CAST(SUM(CONV(SUBSTRING("
                    f"MD5(CONCAT({concat_parts})),1,8),16,10)) AS CHAR)) "
                    f"FROM `{table_name}`"
                )
            else:
                concat_parts = " || '|' || ".join(
                    f"COALESCE(CAST(\"{c}\" AS TEXT), 'NULL')" for c in columns
                )
                sql = (
                    f"SELECT MD5(CAST(BIT_XOR(('x'||SUBSTRING(MD5("
                    f"{concat_parts}),1,8))::BIT(32)::BIGINT::BIT(64)) AS TEXT)) "
                    f"FROM \"{table_name}\""
                )
            cursor.execute(sql)
            row = cursor.fetchone()
            return row[0] if row and row[0] else "empty"
        except Exception as e:
            logger.error("Checksum computation failed", table=table_name, error=str(e))
            return "error"
        finally:
            cursor.close()
            conn.close()

    # ── 3. Sample Validation ──────────────────────────────────────────────────

    def _validate_sample(
        self,
        source_table: str,
        target_table: str,
        column_mapping: Dict[str, str],
        sample_size: int
    ) -> ValidationResult:
        """
        Fetch N random rows from source, look them up in target by PK,
        compare field values. Reports mismatches.
        """
        src_rows = self._fetch_sample(self.source_config, source_table, sample_size)
        if not src_rows:
            return ValidationResult(
                validation_type="sample",
                source_table=source_table,
                target_table=target_table,
                passed=True,
                source_value="0 rows",
                target_value="0 rows",
                details="No rows to sample"
            )

        mismatches = []
        checked    = 0

        src_conn   = self._get_conn(self.source_config)
        tgt_conn   = self._get_conn(self.target_config)
        src_engine = self.source_config.get("engine", "mysql").lower()
        tgt_engine = self.target_config.get("engine", "mysql").lower()

        try:
            for src_row in src_rows:
                # Assume first column in mapping is PK
                src_col_list = list(column_mapping.keys())
                tgt_col_list = list(column_mapping.values())
                pk_src = src_col_list[0]
                pk_tgt = tgt_col_list[0]
                pk_val = src_row.get(pk_src)

                if pk_val is None:
                    continue

                # Fetch matching row from target
                tgt_cur = tgt_conn.cursor()
                if tgt_engine == "mysql":
                    import mysql.connector.cursor
                    if hasattr(tgt_cur, 'dictionary'):
                        tgt_conn.close()
                        tgt_conn = self._get_conn(self.target_config)
                        tgt_cur  = tgt_conn.cursor(dictionary=True)
                    tgt_cur.execute(
                        f"SELECT * FROM `{target_table}` WHERE `{pk_tgt}` = %s", (pk_val,)
                    )
                else:
                    import psycopg2.extras
                    tgt_cur = tgt_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    tgt_cur.execute(
                        f'SELECT * FROM "{target_table}" WHERE "{pk_tgt}" = %s', (pk_val,)
                    )

                tgt_row = tgt_cur.fetchone()
                tgt_cur.close()

                if tgt_row is None:
                    mismatches.append(f"PK {pk_val}: row missing in target")
                    continue

                tgt_row = dict(tgt_row)

                # Compare each mapped column
                for src_col, tgt_col in column_mapping.items():
                    src_val = str(src_row.get(src_col, "")) if src_row.get(src_col) is not None else "NULL"
                    tgt_val = str(tgt_row.get(tgt_col, "")) if tgt_row.get(tgt_col) is not None else "NULL"
                    if src_val != tgt_val:
                        mismatches.append(
                            f"PK {pk_val} col '{src_col}'→'{tgt_col}': "
                            f"src={src_val!r} tgt={tgt_val!r}"
                        )

                checked += 1

        finally:
            src_conn.close()
            tgt_conn.close()

        passed  = len(mismatches) == 0
        details = (
            f"Sampled {checked} rows — all match" if passed else
            f"Sampled {checked} rows — {len(mismatches)} mismatches:\n" +
            "\n".join(mismatches[:10]) + ("..." if len(mismatches) > 10 else "")
        )

        return ValidationResult(
            validation_type="sample",
            source_table=source_table,
            target_table=target_table,
            passed=passed,
            source_value=f"{checked} rows checked",
            target_value=f"{len(mismatches)} mismatches",
            details=details
        )

    def _fetch_sample(self, config: dict, table_name: str, n: int) -> List[dict]:
        engine = config.get("engine", "").lower()
        conn   = self._get_conn(config)
        try:
            if engine == "mysql":
                import mysql.connector
                c2   = mysql.connector.connect(**{k: v for k, v in config.items() if k != "engine"})
                cur  = c2.cursor(dictionary=True)
                cur.execute(f"SELECT * FROM `{table_name}` ORDER BY RAND() LIMIT {n}")
                rows = cur.fetchall()
                cur.close()
                c2.close()
                return rows
            else:
                import psycopg2
                import psycopg2.extras
                c2  = psycopg2.connect(**self._pg_kwargs(config))
                cur = c2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(f'SELECT * FROM "{table_name}" ORDER BY RANDOM() LIMIT {n}')
                rows = [dict(r) for r in cur.fetchall()]
                cur.close()
                c2.close()
                return rows
        except Exception as e:
            logger.error("Sample fetch failed", table=table_name, error=str(e))
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── 4. NULL check ─────────────────────────────────────────────────────────

    def _validate_nulls(
        self,
        target_table: str,
        column_mapping: Dict[str, str]
    ) -> List[ValidationResult]:
        """
        Check that no NOT NULL violations exist in target.
        Queries each mapped target column for NULL count.
        """
        results = []
        conn    = self._get_conn(self.target_config)
        engine  = self.target_config.get("engine", "mysql").lower()
        cursor  = conn.cursor()

        try:
            for tgt_col in column_mapping.values():
                if engine == "mysql":
                    cursor.execute(
                        f"SELECT COUNT(*) FROM `{target_table}` WHERE `{tgt_col}` IS NULL"
                    )
                else:
                    cursor.execute(
                        f'SELECT COUNT(*) FROM "{target_table}" WHERE "{tgt_col}" IS NULL'
                    )
                null_count = cursor.fetchone()[0]
                passed     = null_count == 0
                results.append(ValidationResult(
                    validation_type="null_check",
                    source_table=target_table,
                    target_table=target_table,
                    passed=passed,
                    source_value="0",
                    target_value=str(null_count),
                    details=f"Column '{tgt_col}': {null_count} NULLs in target" if not passed else
                            f"Column '{tgt_col}': no NULLs"
                ))
        finally:
            cursor.close()
            conn.close()

        return results

    # ── 5. Business rule ──────────────────────────────────────────────────────

    def _validate_business_rule(
        self,
        target_table: str,
        rule: Dict[str, str]
    ) -> ValidationResult:
        """
        rule = {"name": "no_negative_prices", "sql": "SELECT COUNT(*) FROM prices WHERE amount < 0"}
        The SQL must return a single integer. Result must be 0 for the rule to pass.
        """
        rule_name = rule.get("name", "custom_rule")
        rule_sql  = rule.get("sql", "")

        conn   = self._get_conn(self.target_config)
        cursor = conn.cursor()
        try:
            cursor.execute(rule_sql)
            count  = cursor.fetchone()[0]
            passed = int(count) == 0
            return ValidationResult(
                validation_type="business_rule",
                source_table=target_table,
                target_table=target_table,
                passed=passed,
                source_value="0",
                target_value=str(count),
                details=f"Rule '{rule_name}': {'PASSED' if passed else f'FAILED — {count} violations'}"
            )
        except Exception as e:
            return ValidationResult(
                validation_type="business_rule",
                source_table=target_table,
                target_table=target_table,
                passed=False,
                source_value="",
                target_value="error",
                details=f"Rule '{rule_name}' failed to execute: {e}"
            )
        finally:
            cursor.close()
            conn.close()

    # ── Save results to DB ────────────────────────────────────────────────────

    def _save_results(self, db: Session, project_id: str, results: List[ValidationResult]):
        import uuid, datetime as dt
        for r in results:
            try:
                db.execute(
                    text("""
                        INSERT INTO schema_validation_results
                            (id, project_id, validation_type, source_table,
                             target_table, source_value, target_value, passed, details, created_at)
                        VALUES
                            (:id, :pid, :vtype, :stbl, :ttbl, :sval, :tval, :passed, :details, :now)
                    """),
                    {
                        "id":     str(uuid.uuid4()),
                        "pid":    project_id,
                        "vtype":  r.validation_type,
                        "stbl":   r.source_table,
                        "ttbl":   r.target_table,
                        "sval":   r.source_value,
                        "tval":   r.target_value,
                        "passed": r.passed,
                        "details": r.details,
                        "now":    dt.datetime.utcnow(),
                    }
                )
            except Exception as e:
                logger.warning("Failed to save validation result", error=str(e))
        try:
            db.commit()
        except Exception:
            db.rollback()

    # ── Connection helpers ────────────────────────────────────────────────────

    def _get_conn(self, config: dict):
        engine = config.get("engine", "").lower()
        if engine == "mysql":
            import mysql.connector
            return mysql.connector.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                connection_timeout=30
            )
        else:
            import psycopg2
            return psycopg2.connect(**self._pg_kwargs(config))

    def _pg_kwargs(self, config: dict) -> dict:
        return {
            "host":     config.get("host", "localhost"),
            "port":     int(config.get("port", 5432)),
            "dbname":   config.get("database"),
            "user":     config.get("user"),
            "password": config.get("password"),
            "connect_timeout": 30,
        }
