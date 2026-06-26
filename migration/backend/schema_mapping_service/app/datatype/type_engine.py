"""
Data Type Conversion Engine
File: migration/backend/schema_mapping_service/app/datatype/type_engine.py

Enterprise upgrade of the old tool's is_conversion_safe() + get_cast_type().

Key improvements:
  - Rules stored in datatype_conversion_rules DB table (configurable per tenant)
  - In-memory fallback when DB unavailable
  - Cross-engine awareness (MySQL→PostgreSQL and vice versa)
  - Returns structured ConversionResult, not just a string
  - Generates correct CAST syntax per engine
"""

from typing import Optional, Dict
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.schema_mapping_service.app.comparison.schema_comparator import (
    get_base_type, conversion_safety
)
from backend.shared.config.logging import logger


@dataclass
class ConversionResult:
    source_type:    str
    target_type:    str
    safety:         str          # safe | lossy | unsafe | conditional
    requires_cast:  bool
    cast_expression: Optional[str]   # e.g. "CAST(`col` AS BIGINT)"
    notes:          Optional[str]
    action:         str          # proceed | warn | block | manual_review


class DataTypeEngine:

    def __init__(self, db: Session = None, tenant_id: str = "global"):
        self.db        = db
        self.tenant_id = tenant_id
        self._cache: Dict[str, ConversionResult] = {}

    def analyze(
        self,
        source_col_ref: str,     # e.g. "`users`.`id`"
        source_type:    str,
        target_type:    str,
        source_db:      str = "mysql",
        target_db:      str = "mysql",
    ) -> ConversionResult:
        """
        Analyze a type conversion and return full ConversionResult.
        Checks DB rules first, falls back to in-memory logic.
        """
        cache_key = f"{source_type}|{target_type}|{source_db}|{target_db}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Substitute actual column reference into cached cast_expression
            result = ConversionResult(
                source_type=cached.source_type,
                target_type=cached.target_type,
                safety=cached.safety,
                requires_cast=cached.requires_cast,
                cast_expression=self._substitute_col(cached.cast_expression, source_col_ref),
                notes=cached.notes,
                action=cached.action,
            )
            return result

        src_base = get_base_type(source_type)
        tgt_base = get_base_type(target_type)

        # Try DB rules
        rule = self._fetch_rule(src_base, tgt_base, source_db, target_db)

        if rule:
            safety      = rule["safety"]
            cast_tmpl   = rule["cast_template"]
            notes       = rule["notes"]
        else:
            # Fallback to in-memory logic
            safety      = conversion_safety(source_type, target_type)
            cast_tmpl   = self._default_cast_template(tgt_base, source_db)
            notes       = None

        requires_cast   = (src_base != tgt_base)
        cast_expression = None
        if requires_cast and cast_tmpl:
            cast_expression = self._substitute_col(cast_tmpl, source_col_ref)

        action = self._determine_action(safety)

        result = ConversionResult(
            source_type=source_type,
            target_type=target_type,
            safety=safety,
            requires_cast=requires_cast,
            cast_expression=cast_expression,
            notes=notes,
            action=action,
        )

        # Cache without the specific column reference
        self._cache[cache_key] = ConversionResult(
            source_type=source_type, target_type=target_type,
            safety=safety, requires_cast=requires_cast,
            cast_expression=cast_tmpl, notes=notes, action=action
        )

        return result

    def analyze_table(
        self,
        source_cols: Dict[str, dict],
        target_cols: Dict[str, dict],
        col_mapping: Dict[str, str],     # {src_col: tgt_col}
        source_table: str = "",
        source_db: str = "mysql",
        target_db: str = "mysql",
    ) -> Dict[str, ConversionResult]:
        """
        Analyze all column conversions for a table mapping.
        Returns {target_col: ConversionResult}
        """
        results = {}
        for src_col, tgt_col in col_mapping.items():
            src_def  = source_cols.get(src_col, {})
            tgt_def  = target_cols.get(tgt_col, {})
            src_type = src_def.get("type", "")
            tgt_type = tgt_def.get("type", "")

            if not src_type or not tgt_type:
                continue

            col_ref = f"`{source_table}`.`{src_col}`" if source_table else f"`{src_col}`"
            results[tgt_col] = self.analyze(col_ref, src_type, tgt_type, source_db, target_db)

        return results

    # ── Private helpers ────────────────────────────────────────────────────────

    def _fetch_rule(self, src_base, tgt_base, source_db, target_db) -> Optional[dict]:
        if not self.db:
            return None
        try:
            row = self.db.execute(
                text("""
                    SELECT safety, cast_template, notes
                    FROM datatype_conversion_rules
                    WHERE (tenant_id = :tid OR tenant_id = 'global')
                      AND (source_db = :sdb OR source_db = 'any')
                      AND (target_db = :tdb OR target_db = 'any')
                      AND source_type = :src
                      AND target_type = :tgt
                    ORDER BY
                        CASE WHEN tenant_id = :tid THEN 0 ELSE 1 END,
                        CASE WHEN source_db  = :sdb THEN 0 ELSE 1 END,
                        CASE WHEN target_db  = :tdb THEN 0 ELSE 1 END
                    LIMIT 1
                """),
                {"tid": self.tenant_id, "sdb": source_db, "tdb": target_db,
                 "src": src_base, "tgt": tgt_base}
            ).fetchone()
            return dict(row._mapping) if row else None
        except Exception as e:
            logger.warning("DB rule lookup failed, using fallback", error=str(e))
            return None

    def _substitute_col(self, template: Optional[str], col_ref: str) -> Optional[str]:
        if not template:
            return None
        return template.replace("{col}", col_ref)

    def _default_cast_template(self, target_base: str, source_db: str) -> Optional[str]:
        if source_db == "mysql":
            mysql_casts = {
                "bigint": "CAST({col} AS SIGNED)",
                "int": "CAST({col} AS SIGNED)",
                "smallint": "CAST({col} AS SIGNED)",
                "decimal": "CAST({col} AS DECIMAL(18,4))",
                "double": "CAST({col} AS DOUBLE)",
                "float": "CAST({col} AS DOUBLE)",
                "varchar": "CAST({col} AS CHAR)",
                "char": "CAST({col} AS CHAR)",
                "text": "CAST({col} AS CHAR)",
                "date": "DATE({col})",
                "datetime": "CAST({col} AS DATETIME)",
            }
            return mysql_casts.get(target_base)
        else:
            pg_casts = {
                "bigint": "({col})::BIGINT",
                "int": "({col})::INT",
                "text": "({col})::TEXT",
                "varchar": "({col})::TEXT",
                "date": "({col})::DATE",
                "timestamp": "({col})::TIMESTAMP",
                "json": "({col})::JSON",
                "jsonb": "({col})::JSONB",
                "boolean": "({col})::BOOLEAN",
            }
            return pg_casts.get(target_base)

    def _determine_action(self, safety: str) -> str:
        return {
            "safe":        "proceed",
            "lossy":       "warn",
            "unsafe":      "block",
            "conditional": "manual_review",
        }.get(safety, "block")
