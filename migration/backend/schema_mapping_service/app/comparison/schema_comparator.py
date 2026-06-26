"""
Schema Comparison Engine
File: migration/backend/schema_mapping_service/app/comparison/schema_comparator.py

Compares source schema vs target schema and produces a full structured diff.
Key upgrade from the old tool: detects RENAMES using Levenshtein similarity
instead of treating renamed columns as drop+add.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


# ── Levenshtein similarity ─────────────────────────────────────────────────────

def levenshtein_similarity(s1: str, s2: str) -> float:
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1): dp[i][0] = i
    for j in range(len2 + 1): dp[0][j] = j
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(dp[i-1][j]+1, dp[i][j-1]+1, dp[i-1][j-1]+cost)
    return round(1.0 - dp[len1][len2] / max(len1, len2), 3)


def get_base_type(dtype: str) -> str:
    """'varchar(255)' → 'varchar'"""
    if not dtype:
        return ""
    return dtype.split("(")[0].split()[0].lower()


# ── Dataclasses for diff results ───────────────────────────────────────────────

@dataclass
class ColumnDiff:
    column_name:   str
    status:        str           # matching | changed | renamed | added | removed
    old_name:      Optional[str] = None
    old_type:      Optional[str] = None
    new_type:      Optional[str] = None
    old_nullable:  Optional[bool] = None
    new_nullable:  Optional[bool] = None
    old_default:   Any = None
    new_default:   Any = None
    conversion_safety: Optional[str] = None
    change_details: List[str] = field(default_factory=list)


@dataclass
class TableDiff:
    table_name:    str
    status:        str           # matching | changed | added | removed
    column_diffs:  List[ColumnDiff] = field(default_factory=list)
    pk_changed:    bool = False
    fk_changes:    List[str] = field(default_factory=list)
    index_changes: List[str] = field(default_factory=list)
    source_row_count: int = 0


@dataclass
class SchemaDiff:
    source_name:      str
    target_name:      str
    tables_added:     List[str] = field(default_factory=list)
    tables_removed:   List[str] = field(default_factory=list)
    tables_changed:   List[TableDiff] = field(default_factory=list)
    tables_matching:  List[str] = field(default_factory=list)
    risk_level:       str = "low"
    unsafe_conversions: int = 0
    lossy_conversions:  int = 0
    summary:          Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_name":      self.source_name,
            "target_name":      self.target_name,
            "tables_added":     self.tables_added,
            "tables_removed":   self.tables_removed,
            "tables_matching":  self.tables_matching,
            "tables_changed":   [_tbl_to_dict(t) for t in self.tables_changed],
            "risk_level":       self.risk_level,
            "unsafe_conversions": self.unsafe_conversions,
            "lossy_conversions":  self.lossy_conversions,
            "summary":          self.summary,
        }


def _tbl_to_dict(t: TableDiff) -> dict:
    return {
        "table_name":    t.table_name,
        "status":        t.status,
        "pk_changed":    t.pk_changed,
        "fk_changes":    t.fk_changes,
        "index_changes": t.index_changes,
        "source_row_count": t.source_row_count,
        "column_diffs": [
            {
                "column_name":      c.column_name,
                "status":           c.status,
                "old_name":         c.old_name,
                "old_type":         c.old_type,
                "new_type":         c.new_type,
                "old_nullable":     c.old_nullable,
                "new_nullable":     c.new_nullable,
                "old_default":      str(c.old_default) if c.old_default is not None else None,
                "new_default":      str(c.new_default) if c.new_default is not None else None,
                "conversion_safety": c.conversion_safety,
                "change_details":   c.change_details,
            }
            for c in t.column_diffs
        ],
    }


# ── Type categories for safety checking ───────────────────────────────────────

_INT_TYPES     = {"tinyint","smallint","mediumint","int","integer","bigint","int2","int4","int8","serial","bigserial"}
_FLOAT_TYPES   = {"float","double","real","float4","float8"}
_DECIMAL_TYPES = {"decimal","numeric","fixed","money"}
_STR_TYPES     = {"char","varchar","tinytext","text","mediumtext","longtext","enum","set","bpchar","character varying","nvarchar","nchar"}
_DATE_TYPES    = {"date","datetime","timestamp","timestamptz","time","timetz","year","interval"}
_BINARY_TYPES  = {"binary","varbinary","tinyblob","blob","mediumblob","longblob","bit","bytea"}
_JSON_TYPES    = {"json","jsonb"}
_BOOL_TYPES    = {"boolean","bool"}
_UUID_TYPES    = {"uuid"}


def _cat(t: str) -> str:
    b = get_base_type(t)
    if b in _INT_TYPES:     return "int"
    if b in _FLOAT_TYPES:   return "float"
    if b in _DECIMAL_TYPES: return "decimal"
    if b in _STR_TYPES:     return "str"
    if b in _DATE_TYPES:    return "date"
    if b in _BINARY_TYPES:  return "binary"
    if b in _JSON_TYPES:    return "json"
    if b in _BOOL_TYPES:    return "bool"
    if b in _UUID_TYPES:    return "uuid"
    return "other"


def conversion_safety(old_type: str, new_type: str) -> str:
    """
    Returns: safe | lossy | unsafe | conditional
    This is the expanded version of the original is_conversion_safe().
    Now handles cross-engine types (MySQL ↔ PostgreSQL).
    """
    ob, nb = get_base_type(old_type), get_base_type(new_type)
    if ob == nb:
        return "safe"
    oc, nc = _cat(old_type), _cat(new_type)

    # Anything → string
    if nc == "str":
        return "safe" if oc not in ("binary",) else "lossy"

    # String → JSON (conditional on content)
    if oc == "str" and nc == "json":
        return "conditional"

    # JSON ↔ string
    if oc == "json" and nc == "str":
        return "safe"

    # Numeric
    if oc in ("int","float","decimal") and nc in ("int","float","decimal"):
        if nc == "int" and oc in ("float","decimal"):
            return "lossy"
        if nc == "float":
            return "lossy"
        if oc == "int" and nc == "decimal":
            return "safe"
        return "lossy"

    # Int ↔ bool
    if oc == "int" and nc == "bool":
        return "lossy"
    if oc == "bool" and nc == "int":
        return "safe"

    # Date
    if oc == "date" and nc == "date":
        safe_pairs = {("date","datetime"),("date","timestamp"),("date","timestamptz"),("datetime","timestamp"),("timestamp","datetime")}
        if (ob, nb) in safe_pairs:
            return "safe"
        return "lossy"

    # String → date
    if oc == "str" and nc == "date":
        return "conditional"

    # UUID
    if oc == "uuid" and nc == "str":
        return "safe"
    if oc == "str" and nc == "uuid":
        return "conditional"

    # Binary
    if oc == "binary" and nc == "binary":
        return "safe"

    return "unsafe"


def get_cast_expression(source_col: str, target_type: str, source_db: str = "mysql") -> Optional[str]:
    """
    Generate a SQL CAST expression for converting a column.
    source_col: the column reference e.g. "`users`.`id`"
    """
    base = get_base_type(target_type)

    if source_db == "mysql":
        mapping = {
            "bigint": f"CAST({source_col} AS SIGNED)",
            "int": f"CAST({source_col} AS SIGNED)",
            "smallint": f"CAST({source_col} AS SIGNED)",
            "tinyint": f"CAST({source_col} AS UNSIGNED)",
            "decimal": f"CAST({source_col} AS DECIMAL(18,4))",
            "double": f"CAST({source_col} AS DOUBLE)",
            "float": f"CAST({source_col} AS DOUBLE)",
            "varchar": f"CAST({source_col} AS CHAR)",
            "char": f"CAST({source_col} AS CHAR)",
            "text": f"CAST({source_col} AS CHAR)",
            "date": f"DATE({source_col})",
            "datetime": f"CAST({source_col} AS DATETIME)",
            "timestamp": f"CAST({source_col} AS DATETIME)",
        }
    else:
        mapping = {
            "bigint": f"({source_col})::BIGINT",
            "int": f"({source_col})::INT",
            "text": f"({source_col})::TEXT",
            "varchar": f"({source_col})::TEXT",
            "date": f"({source_col})::DATE",
            "timestamp": f"({source_col})::TIMESTAMP",
            "json": f"({source_col})::JSON",
            "jsonb": f"({source_col})::JSONB",
            "boolean": f"({source_col})::BOOLEAN",
        }

    return mapping.get(base)


# ── Main comparator ────────────────────────────────────────────────────────────

class SchemaComparator:

    RENAME_THRESHOLD = 0.65   # Levenshtein score to flag as "likely rename"

    def compare(
        self,
        source_schema: Dict[str, Any],
        target_schema: Dict[str, Any],
        column_mappings: Dict[str, Dict[str, str]] = None
    ) -> SchemaDiff:
        """
        Compare two schemas.
        column_mappings = {source_table: {source_col: target_col}}
        Providing this lets the comparator know about intentional renames.
        """
        column_mappings = column_mappings or {}
        src_tables = set(source_schema.get("tables", {}).keys())
        tgt_tables = set(target_schema.get("tables", {}).keys())

        diff = SchemaDiff(
            source_name=source_schema.get("database", "source"),
            target_name=target_schema.get("database", "target"),
            tables_added=sorted(tgt_tables - src_tables),
            tables_removed=sorted(src_tables - tgt_tables),
        )

        for tname in sorted(src_tables & tgt_tables):
            src_tbl = source_schema["tables"][tname]
            tgt_tbl = target_schema["tables"][tname]
            col_map = column_mappings.get(tname, {})
            tbl_diff = self._compare_table(tname, src_tbl, tgt_tbl, col_map)

            if tbl_diff.status == "matching":
                diff.tables_matching.append(tname)
            else:
                diff.tables_changed.append(tbl_diff)

        # Count risks
        for td in diff.tables_changed:
            for cd in td.column_diffs:
                if cd.conversion_safety == "unsafe":
                    diff.unsafe_conversions += 1
                elif cd.conversion_safety == "lossy":
                    diff.lossy_conversions += 1

        if diff.unsafe_conversions > 0:
            diff.risk_level = "high"
        elif diff.lossy_conversions > 5 or len(diff.tables_removed) > 2:
            diff.risk_level = "medium"

        diff.summary = {
            "tables_total":       len(src_tables),
            "tables_added":       len(diff.tables_added),
            "tables_removed":     len(diff.tables_removed),
            "tables_changed":     len(diff.tables_changed),
            "tables_matching":    len(diff.tables_matching),
            "unsafe_conversions": diff.unsafe_conversions,
            "lossy_conversions":  diff.lossy_conversions,
            "risk_level":         diff.risk_level,
        }
        return diff

    def _compare_table(self, tname, src_tbl, tgt_tbl, col_map):
        src_cols = src_tbl.get("columns", {})
        tgt_cols = tgt_tbl.get("columns", {})
        rev_map  = {v: k for k, v in col_map.items()}

        td = TableDiff(
            table_name=tname,
            status="matching",
            source_row_count=src_tbl.get("row_count", 0)
        )

        # PK change
        src_pks = set(src_tbl.get("primary_keys", []))
        tgt_pks = set(tgt_tbl.get("primary_keys", []))
        if src_pks != tgt_pks:
            td.pk_changed = True
            td.status = "changed"
            td.fk_changes.append(f"PK changed: {src_pks} → {tgt_pks}")

        # FK changes
        src_fk_cols = {fk["column"] for fk in src_tbl.get("foreign_keys", [])}
        tgt_fk_cols = {fk["column"] for fk in tgt_tbl.get("foreign_keys", [])}
        for col in tgt_fk_cols - src_fk_cols:
            td.fk_changes.append(f"FK added on column: {col}")
            td.status = "changed"
        for col in src_fk_cols - tgt_fk_cols:
            td.fk_changes.append(f"FK removed on column: {col}")
            td.status = "changed"

        # Column comparison
        processed_src = set()

        for tgt_col_name, tgt_def in tgt_cols.items():
            src_col_name = rev_map.get(tgt_col_name, tgt_col_name)

            if src_col_name in src_cols:
                processed_src.add(src_col_name)
                cd = self._compare_column(src_col_name, tgt_col_name, src_cols[src_col_name], tgt_def)
                td.column_diffs.append(cd)
                if cd.status != "matching":
                    td.status = "changed"
            else:
                # Check for rename
                best, score = self._best_rename(
                    tgt_col_name,
                    {k: v for k, v in src_cols.items() if k not in processed_src}
                )
                if best and score >= self.RENAME_THRESHOLD:
                    processed_src.add(best)
                    src_def = src_cols[best]
                    old_type = src_def.get("type", "")
                    new_type = tgt_def.get("type", "")
                    cd = ColumnDiff(
                        column_name=tgt_col_name,
                        status="renamed",
                        old_name=best,
                        old_type=old_type,
                        new_type=new_type,
                        old_nullable=src_def.get("nullable"),
                        new_nullable=tgt_def.get("nullable"),
                        conversion_safety=conversion_safety(old_type, new_type),
                        change_details=[f"Likely rename from '{best}' (similarity {score:.0%})"]
                    )
                    td.column_diffs.append(cd)
                    td.status = "changed"
                else:
                    cd = ColumnDiff(
                        column_name=tgt_col_name,
                        status="added",
                        new_type=tgt_def.get("type"),
                        new_nullable=tgt_def.get("nullable"),
                    )
                    td.column_diffs.append(cd)
                    td.status = "changed"

        for src_col in set(src_cols) - processed_src:
            src_def = src_cols[src_col]
            td.column_diffs.append(ColumnDiff(
                column_name=src_col,
                status="removed",
                old_type=src_def.get("type"),
                old_nullable=src_def.get("nullable"),
            ))
            td.status = "changed"

        return td

    def _compare_column(self, src_name, tgt_name, src_def, tgt_def) -> ColumnDiff:
        old_type     = src_def.get("type", "")
        new_type     = tgt_def.get("type", "")
        old_nullable = src_def.get("nullable")
        new_nullable = tgt_def.get("nullable")
        old_default  = src_def.get("default")
        new_default  = tgt_def.get("default")
        is_renamed   = src_name != tgt_name

        changes = []
        safety = None
        if get_base_type(old_type) != get_base_type(new_type):
            safety = conversion_safety(old_type, new_type)
            changes.append(f"Type: {old_type} → {new_type} [{safety}]")
        if old_nullable != new_nullable:
            changes.append(f"Nullable: {old_nullable} → {new_nullable}")
        if str(old_default) != str(new_default):
            changes.append(f"Default: {old_default!r} → {new_default!r}")

        status = "renamed" if is_renamed else ("changed" if changes else "matching")
        return ColumnDiff(
            column_name=tgt_name, status=status,
            old_name=src_name if is_renamed else None,
            old_type=old_type, new_type=new_type,
            old_nullable=old_nullable, new_nullable=new_nullable,
            old_default=old_default, new_default=new_default,
            conversion_safety=safety, change_details=changes
        )

    def _best_rename(self, target_col: str, candidates: dict):
        best, score = None, 0.0
        for c in candidates:
            s = levenshtein_similarity(c, target_col)
            if s > score:
                score, best = s, c
        return best, score
