"""
Transformation Engine
File: migration/backend/schema_mapping_service/app/transformation_engine/transformer.py

Applies column-level transformations during worker execution.
Supports all 6 mapping kinds from the original tool (expanded):

  1. direct      → copy value as-is (with optional CAST)
  2. rename      → copy from differently-named source column
  3. transform   → apply a Python expression string
  4. constant    → always write a fixed literal value
  5. expression  → compute from multiple source columns
  6. lookup      → look up value in another table (via pre-loaded cache)

Also generates SQL SELECT expressions for bulk INSERT ... SELECT operations.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from backend.shared.config.logging import logger


@dataclass
class ColumnMappingConfig:
    source_column:    str
    target_column:    str
    source_type:      str
    target_type:      str
    mapping_kind:     str                      # direct|rename|transform|constant|expression|lookup
    mapping_config:   Optional[Dict] = None    # extra config per kind
    requires_cast:    bool = False
    cast_expression:  Optional[str] = None     # e.g. "CAST(`col` AS BIGINT)"
    conversion_safety: str = "safe"


class RowTransformer:
    """Transforms one source row dict into a target row dict."""

    def __init__(self, column_mappings: List[ColumnMappingConfig], lookup_cache: Dict = None):
        self.mappings     = column_mappings
        self.lookup_cache = lookup_cache or {}

    def transform(self, source_row: Dict[str, Any]) -> Dict[str, Any]:
        target_row = {}
        for m in self.mappings:
            try:
                target_row[m.target_column] = self._apply(m, source_row, target_row)
            except Exception as e:
                logger.error("Column transform failed",
                             src=m.source_column, tgt=m.target_column, error=str(e))
                raise ValueError(f"Transform failed for '{m.target_column}': {e}") from e
        return target_row

    def _apply(self, m: ColumnMappingConfig, src: Dict, tgt: Dict) -> Any:
        kind   = m.mapping_kind
        config = m.mapping_config or {}

        if kind in ("direct", "rename"):
            return self._cast(src.get(m.source_column), m)

        elif kind == "transform":
            expr = config.get("expression", "")
            if not expr:
                raise ValueError("transform mapping missing 'expression'")
            return self._cast(self._eval(expr, src), m)

        elif kind == "constant":
            return config.get("value")

        elif kind == "expression":
            expr = config.get("expression", "")
            if not expr:
                raise ValueError("expression mapping missing 'expression'")
            return self._eval(expr, src)

        elif kind == "lookup":
            lookup_table = config.get("table", "")
            key_col      = config.get("key_col", "")
            src_value    = src.get(m.source_column)
            cache_key    = f"{lookup_table}.{key_col}.{src_value}"
            if cache_key in self.lookup_cache:
                return self.lookup_cache[cache_key]
            logger.warning("Lookup cache miss", table=lookup_table, key=src_value)
            return src_value   # fallback

        raise ValueError(f"Unknown mapping_kind: '{kind}'")

    def _cast(self, value: Any, m: ColumnMappingConfig) -> Any:
        if value is None:
            return None
        tgt_base = m.target_type.split("(")[0].lower() if m.target_type else ""
        try:
            if tgt_base in ("int", "integer", "bigint", "smallint", "tinyint"):
                return int(float(str(value).replace(",", "")))
            if tgt_base in ("float", "double", "real"):
                return float(str(value).replace(",", ""))
            if tgt_base in ("decimal", "numeric"):
                import decimal
                return decimal.Decimal(str(value).replace(",", ""))
            if tgt_base in ("varchar", "char", "text", "longtext", "mediumtext", "tinytext"):
                return str(value)
            if tgt_base in ("bool", "boolean"):
                if isinstance(value, bool):
                    return value
                return str(value).lower() in ("true", "yes", "1", "y")
            if tgt_base == "date":
                import datetime
                if isinstance(value, datetime.datetime):
                    return value.date()
                return value
            if tgt_base in ("datetime", "timestamp", "timestamptz"):
                import datetime
                if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
                    return datetime.datetime(value.year, value.month, value.day)
                return value
            return value
        except (ValueError, TypeError) as e:
            logger.warning("Cast failed, returning original", value=str(value)[:40], error=str(e))
            return value

    def _eval(self, expr: str, src: Dict) -> Any:
        """Safe eval with whitelisted builtins only."""
        safe = {
            "str": str, "int": int, "float": float, "bool": bool,
            "len": len, "round": round, "abs": abs, "max": max, "min": min,
            "None": None, "True": True, "False": False,
        }
        try:
            return eval(expr, {"__builtins__": safe}, {"row": src})
        except Exception as e:
            raise ValueError(f"Expression eval failed: '{expr}' → {e}")


def build_transformer(column_mappings_from_db: list) -> RowTransformer:
    """Build a RowTransformer from DB ColumnMapping objects."""
    configs = [
        ColumnMappingConfig(
            source_column=m.source_column or "",
            target_column=m.target_column or "",
            source_type=m.source_type or "",
            target_type=m.target_type or "",
            mapping_kind=m.mapping_kind or "direct",
            mapping_config=m.mapping_config or {},
            requires_cast=m.requires_cast or False,
            cast_expression=m.cast_expression,
            conversion_safety=m.conversion_safety or "safe",
        )
        for m in column_mappings_from_db
    ]
    return RowTransformer(configs)


def build_sql_select_expr(m: ColumnMappingConfig) -> str:
    """
    Build the SQL SELECT expression for a column mapping.
    Used in bulk INSERT ... SELECT.
    """
    src, tgt   = m.source_column, m.target_column
    kind       = m.mapping_kind
    config     = m.mapping_config or {}

    if kind == "constant":
        val = config.get("value", "")
        if isinstance(val, str):
            val = f"'{val}'"
        return f"{val} AS `{tgt}`"

    if kind in ("transform", "expression"):
        sql_expr = config.get("sql")
        if sql_expr:
            return f"({sql_expr}) AS `{tgt}`"

    if kind == "lookup":
        lt  = config.get("table", "")
        kc  = config.get("key_col", "")
        vc  = config.get("value_col", "")
        return (f"(SELECT `{vc}` FROM `{lt}` WHERE `{kc}` = `{src}` LIMIT 1) AS `{tgt}`")

    # direct / rename (with optional cast)
    if m.requires_cast and m.cast_expression:
        return f"{m.cast_expression} AS `{tgt}`"
    if src == tgt:
        return f"`{src}`"
    return f"`{src}` AS `{tgt}`"
