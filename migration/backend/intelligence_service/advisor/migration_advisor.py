"""
Migration Advisor
File: migration/backend/intelligence_service/advisor/migration_advisor.py

Rule-based recommendation engine that reasons about the data itself,
not just the schema. Reads from Metadata Catalog (Part 3) and the
dry-run result (Schema Mapping Service).

Key insight: the old Schema Mapping Service's recommendation engine
does fuzzy name matching. The Advisor does DATA reasoning:

  "VARCHAR(50) → VARCHAR(255): safe, but 3 columns have values
   exceeding 50 chars in source — you will lose data."
   (This can only be known after Distribution Analyzer has run)

  "orders.customer_id FK → customers.id: 1:N high fan-out (avg 847
   children per parent). Consider CDC mode — this table grows fast."
   (Only knowable after Relationship Mapper has run)

  "BIGINT → INT: 42 rows exceed INT range. Migration will fail."
   (Only knowable after Distribution Analyzer has run)

Advice categories:
  type_conversion   — safe/lossy/unsafe with data evidence
  index_advice      — missing indexes on FK columns, index bloat
  cdc_recommendation— whether CDC is recommended based on growth rate
  chunk_advice      — per-table chunk size advice based on LOB/skew
  masking_advice    — columns that look like PII (pattern detection)
  ordering_advice   — recommended table migration order

All advice is READ ONLY. The advisor never modifies any state.
"""

import re
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


# PII pattern detection regexes
PII_PATTERNS = {
    "email":          re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"),
    "phone":          re.compile(r"^\+?[\d\s\-\(\)]{7,20}$"),
    "ssn":            re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    "credit_card":    re.compile(r"^\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}$"),
    "ip_address":     re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
}

PII_COLUMN_NAMES = {
    "email", "email_address", "mail", "e_mail",
    "phone", "phone_number", "mobile", "tel", "telephone",
    "ssn", "social_security", "sin", "tax_id",
    "credit_card", "card_number", "ccn", "cvv", "cvc",
    "password", "passwd", "pwd", "secret", "token",
    "dob", "date_of_birth", "birthdate", "birth_date",
    "address", "street", "city", "zip", "postal",
    "ip_address", "ip", "user_ip",
    "salary", "income", "wage",
    "passport", "license", "national_id",
}


@dataclass
class AdviceItem:
    category:    str       # type_conversion | cdc | chunk | masking | index | ordering
    priority:    str       # critical | high | medium | low | info
    table_name:  str
    column_name: Optional[str]
    title:       str
    message:     str
    evidence:    Dict[str, Any] = field(default_factory=dict)
    action:      str = ""

    def to_dict(self) -> dict:
        return {
            "category":    self.category,
            "priority":    self.priority,
            "table_name":  self.table_name,
            "column_name": self.column_name,
            "title":       self.title,
            "message":     self.message,
            "evidence":    self.evidence,
            "action":      self.action,
        }


class MigrationAdvisor:

    def advise(
        self,
        db:             Session,
        connection_id:  str,
        schema_info:    Optional[Dict] = None,    # from connector.discover_schema()
        dry_run_result: Optional[Dict] = None,    # from schema mapping service
        tenant_id:      str = "local",
    ) -> Dict[str, Any]:
        """
        Generate data-aware migration advice.
        Returns categorized advice items sorted by priority.
        """
        logger.info("Migration Advisor running", connection_id=connection_id)

        # Load all metadata catalog data for this connection
        rows = db.execute(
            text("""
                SELECT DISTINCT ON (table_name, catalog_type)
                    table_name, catalog_type, data
                FROM metadata_catalog
                WHERE connection_id = :cid
                ORDER BY table_name, catalog_type, computed_at DESC
            """),
            {"cid": connection_id}
        ).fetchall()

        catalog: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            tname = row.table_name
            ctype = row.catalog_type
            data  = row.data
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            catalog.setdefault(tname, {})[ctype] = data

        all_advice: List[AdviceItem] = []

        for table_name, data in catalog.items():
            all_advice.extend(self._advise_type_conversions(table_name, data, dry_run_result, schema_info))
            all_advice.extend(self._advise_cdc(table_name, data))
            all_advice.extend(self._advise_chunk_strategy(table_name, data))
            all_advice.extend(self._advise_masking(table_name, data, schema_info))
            all_advice.extend(self._advise_indexes(table_name, data, schema_info))

        # Sort: critical first, then high, medium, low, info
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        all_advice.sort(key=lambda a: priority_order.get(a.priority, 5))

        result = {
            "connection_id":  connection_id,
            "total_advice":   len(all_advice),
            "critical_count": sum(1 for a in all_advice if a.priority == "critical"),
            "high_count":     sum(1 for a in all_advice if a.priority == "high"),
            "medium_count":   sum(1 for a in all_advice if a.priority == "medium"),
            "by_category": self._group_by_category(all_advice),
            "advice":         [a.to_dict() for a in all_advice],
        }

        logger.info("Migration Advisor complete",
                    total=len(all_advice),
                    critical=result["critical_count"],
                    high=result["high_count"])
        return result

    # ── Advisors ──────────────────────────────────────────────────────────────

    def _advise_type_conversions(
        self, table_name: str, data: Dict, dry_run: Optional[Dict], schema_info: Optional[Dict]
    ) -> List[AdviceItem]:
        advice = []
        if not dry_run:
            return advice

        unsafe = [u for u in dry_run.get("unsafe_conversions", []) if u.get("table") == table_name]
        lossy  = [l for l in dry_run.get("lossy_conversions", [])  if l.get("table") == table_name]
        dist   = data.get("distribution", {}).get("columns", {})

        for conv in unsafe:
            col     = conv.get("source_column", "")
            from_t  = conv.get("from_type", "")
            to_t    = conv.get("to_type", "")
            col_dist = dist.get(col, {})

            advice.append(AdviceItem(
                category="type_conversion", priority="critical",
                table_name=table_name, column_name=col,
                title=f"Unsafe conversion: {from_t} → {to_t}",
                message=f"Column '{col}' cannot be automatically converted from {from_t} to {to_t}. "
                        "Manual script required.",
                evidence={"from_type": from_t, "to_type": to_t,
                          "sample": col_dist.get("top_values", [])[:3]},
                action="Generate a manual migration script with POST /projects/{id}/scripts/generate",
            ))

        for conv in lossy:
            col     = conv.get("source_column", "")
            from_t  = conv.get("from_type", "")
            to_t    = conv.get("to_type", "")
            col_dist = dist.get(col, {})

            # Check if data actually exceeds target range
            evidence = {"from_type": from_t, "to_type": to_t}
            priority = "high"

            # BIGINT → INT: check if any values exceed INT max (2,147,483,647)
            if "int" in from_t.lower() and "int" in to_t.lower():
                max_val_str = col_dist.get("max_val")
                if max_val_str:
                    try:
                        max_val = int(max_val_str)
                        if max_val > 2_147_483_647:
                            priority = "critical"
                            evidence["max_value"]     = max_val
                            evidence["int_max"]       = 2_147_483_647
                            evidence["overflow_rows"] = "unknown (check manually)"
                    except (ValueError, TypeError):
                        pass

            advice.append(AdviceItem(
                category="type_conversion", priority=priority,
                table_name=table_name, column_name=col,
                title=f"Lossy conversion: {from_t} → {to_t}",
                message=f"Column '{col}' conversion from {from_t} to {to_t} may lose precision or data.",
                evidence=evidence,
                action="Review values before proceeding. Consider widening the target type.",
            ))

        return advice

    def _advise_cdc(self, table_name: str, data: Dict) -> List[AdviceItem]:
        advice = []
        gr = data.get("growth_rate", {})
        stats = data.get("statistics", {})

        rows_per_month = gr.get("rows_per_month", 0) or 0
        row_count      = stats.get("row_count", 0) or 0
        size_gb        = stats.get("size_gb", 0) or 0

        if row_count > 0 and rows_per_month > row_count * 0.05:
            growth_pct = round(rows_per_month / row_count * 100, 1)
            priority   = "high" if growth_pct > 20 else "medium"
            advice.append(AdviceItem(
                category="cdc", priority=priority,
                table_name=table_name, column_name=None,
                title=f"High growth table: +{growth_pct}%/month",
                message=f"Table '{table_name}' grows at {rows_per_month:,.0f} rows/month "
                        f"({growth_pct}% monthly growth). Without CDC, target will be "
                        "significantly behind source by migration end.",
                evidence={"rows_per_month": rows_per_month, "current_rows": row_count,
                          "growth_pct_per_month": growth_pct},
                action="Enable CDC for this table: POST /cdc/sessions with this table in scope.",
            ))

        rel = data.get("relationship", {})
        if isinstance(rel, dict) and "high fan-out" in rel.get("cardinality", ""):
            advice.append(AdviceItem(
                category="cdc", priority="medium",
                table_name=table_name, column_name=None,
                title="High fan-out FK relationship",
                message=f"Table '{table_name}' has high fan-out relationship "
                        f"(avg {rel.get('avg_children_per_parent', 0):.1f} children/parent). "
                        "Parent table must complete migration before this table starts.",
                evidence={"cardinality": rel.get("cardinality"),
                          "avg_children": rel.get("avg_children_per_parent")},
                action="Ensure FK dependency graph is built: POST /jobs/{id}/dependency-graph",
            ))

        return advice

    def _advise_chunk_strategy(self, table_name: str, data: Dict) -> List[AdviceItem]:
        advice = []
        lob   = data.get("lob_detection", {})
        dist  = data.get("distribution", {})
        stats = data.get("statistics", {})

        if lob.get("has_lob"):
            lob_details = lob.get("lob_column_details", {})
            max_lob = max(
                (v.get("max_size_bytes", 0) for v in lob_details.values()),
                default=0
            )
            if max_lob > 1_000_000:
                advice.append(AdviceItem(
                    category="chunk", priority="high",
                    table_name=table_name, column_name=None,
                    title="Reduce chunk size: large LOB columns",
                    message=f"LOB columns up to {max_lob // 1024 // 1024}MB detected. "
                            "Default chunk size will cause excessive memory usage.",
                    evidence={"max_lob_bytes": max_lob, "lob_columns": lob.get("lob_columns", [])},
                    action="Set chunk size ≤ 1,000 rows for this table in adaptive chunk config.",
                ))

        skewed = dist.get("skewed_columns", [])
        if skewed:
            advice.append(AdviceItem(
                category="chunk", priority="medium",
                table_name=table_name, column_name=None,
                title=f"Skewed data: {skewed}",
                message=f"Columns {skewed} have highly skewed value distributions. "
                        "PK-range chunks may have very uneven row counts.",
                evidence={"skewed_columns": skewed},
                action="Use size_based chunk strategy for this table.",
            ))

        return advice

    def _advise_masking(
        self, table_name: str, data: Dict, schema_info: Optional[Dict]
    ) -> List[AdviceItem]:
        advice = []
        if not schema_info:
            return advice

        columns = schema_info.get("tables", {}).get(table_name, {}).get("columns", {})
        dist    = data.get("distribution", {}).get("columns", {})
        pii_found = []

        for col_name, col_def in columns.items():
            col_lower = col_name.lower().replace("-", "_")

            # Name-based PII detection
            if col_lower in PII_COLUMN_NAMES:
                pii_found.append({"column": col_name, "detection": "column_name",
                                  "pattern": col_lower})
                continue

            # Value-pattern-based PII detection (from distribution data)
            col_dist  = dist.get(col_name, {})
            top_values = col_dist.get("top_values", [])
            for tv in top_values[:5]:
                val = str(tv.get("value", ""))
                for pii_type, pattern in PII_PATTERNS.items():
                    if pattern.match(val):
                        pii_found.append({"column": col_name, "detection": "value_pattern",
                                          "pattern": pii_type})
                        break

        if pii_found:
            advice.append(AdviceItem(
                category="masking", priority="high",
                table_name=table_name, column_name=None,
                title=f"PII detected: {len(pii_found)} column(s)",
                message=f"Table '{table_name}' likely contains PII in columns: "
                        f"{[p['column'] for p in pii_found]}. "
                        "Migration to non-production targets should use data masking.",
                evidence={"pii_columns": pii_found},
                action="Configure masking rules: set mapping_kind='mask' for these columns "
                       "in schema_column_mappings. Part 8 adds DataMaskingNode.",
            ))

        return advice

    def _advise_indexes(
        self, table_name: str, data: Dict, schema_info: Optional[Dict]
    ) -> List[AdviceItem]:
        advice = []
        if not schema_info:
            return advice

        table_def = schema_info.get("tables", {}).get(table_name, {})
        fks       = table_def.get("foreign_keys", [])
        indexes   = table_def.get("indexes", [])
        indexed_cols = set()
        for idx in indexes:
            for col in idx.get("columns", []):
                indexed_cols.add(col)

        for fk in fks:
            fk_col = fk.get("column")
            if fk_col and fk_col not in indexed_cols:
                advice.append(AdviceItem(
                    category="index", priority="medium",
                    table_name=table_name, column_name=fk_col,
                    title=f"Missing index on FK column: {fk_col}",
                    message=f"FK column '{fk_col}' has no index. "
                            "Queries joining this table will be slow after migration.",
                    evidence={"fk_column": fk_col,
                              "ref_table": fk.get("ref_table"),
                              "ref_column": fk.get("ref_column")},
                    action=f"CREATE INDEX idx_{table_name}_{fk_col} ON {table_name}({fk_col})",
                ))

        return advice

    def _group_by_category(self, advice: List[AdviceItem]) -> Dict[str, List]:
        grouped: Dict[str, List] = {}
        for item in advice:
            grouped.setdefault(item.category, []).append(item.to_dict())
        return grouped
