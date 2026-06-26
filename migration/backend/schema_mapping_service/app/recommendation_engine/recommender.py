"""
Recommendation Engine
File: migration/backend/schema_mapping_service/app/recommendation_engine/recommender.py

Intelligent auto-mapper. Suggests table and column mappings using:
  1. Exact match         → 1.00
  2. Case-insensitive    → 0.99
  3. Common alias        → 0.92  (cust→customer, addr→address, etc.)
  4. Fuzzy Levenshtein   → 0.60–0.98
  5. Prefix/suffix strip → 0.78  (user_email vs email)

Far smarter than the old tool's exact-name-only auto_map_tables_and_columns().
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from backend.schema_mapping_service.app.comparison.schema_comparator import (
    levenshtein_similarity, get_base_type
)


COMMON_ALIASES = {
    "cust": "customer", "addr": "address", "usr": "user",
    "num": "number", "qty": "quantity", "amt": "amount",
    "nm": "name", "dt": "date", "ts": "timestamp",
    "cd": "code", "desc": "description", "ref": "reference",
    "acct": "account", "dept": "department", "org": "organization",
    "prod": "product", "cat": "category", "inv": "invoice",
    "ord": "order", "emp": "employee", "mgr": "manager",
    "tel": "telephone", "ph": "phone", "mob": "mobile",
    "dob": "date_of_birth", "doj": "date_of_joining",
    "cr": "created", "upd": "updated", "del": "deleted",
    "flg": "flag", "typ": "type", "val": "value", "seq": "sequence",
    "bal": "balance", "tot": "total", "cnt": "count", "avg": "average",
    "max": "maximum", "min": "minimum", "pct": "percent",
    "src": "source", "tgt": "target", "dest": "destination",
}


@dataclass
class Recommendation:
    rec_type:   str      # table_match | column_match | rename_candidate
    source_ref: str      # "users" or "users.first_name"
    target_ref: str      # "customers" or "customers.full_name"
    confidence: float    # 0.0 – 1.0
    reason:     str      # exact_match | alias | fuzzy_0.87 | prefix_suffix

    def to_dict(self) -> dict:
        return {
            "rec_type":   self.rec_type,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "confidence": self.confidence,
            "reason":     self.reason,
            "id":         f"{self.source_ref}→{self.target_ref}",
        }


class RecommendationEngine:

    TABLE_THRESHOLD  = 0.60
    COLUMN_THRESHOLD = 0.60

    def recommend(
        self,
        source_schema: Dict[str, Any],
        target_schema: Dict[str, Any]
    ) -> List[Recommendation]:
        recs: List[Recommendation] = []

        table_recs, table_map = self._recommend_tables(source_schema, target_schema)
        recs.extend(table_recs)

        for src_tbl, tgt_tbl in table_map.items():
            src_cols = source_schema.get("tables", {}).get(src_tbl, {}).get("columns", {})
            tgt_cols = target_schema.get("tables", {}).get(tgt_tbl, {}).get("columns", {})
            if src_cols and tgt_cols:
                recs.extend(self._recommend_columns(src_tbl, tgt_tbl, src_cols, tgt_cols))

        recs.sort(key=lambda r: r.confidence, reverse=True)
        return recs

    def _recommend_tables(
        self,
        source_schema: Dict,
        target_schema: Dict
    ) -> Tuple[List[Recommendation], Dict[str, str]]:
        recs = []
        mapping = {}
        src_tables = list(source_schema.get("tables", {}).keys())
        tgt_tables = list(target_schema.get("tables", {}).keys())
        matched = set()

        for src in src_tables:
            best_tgt, best_score, best_reason = None, 0.0, ""
            for tgt in tgt_tables:
                if tgt in matched:
                    continue
                score, reason = self._score(src, tgt)
                if score > best_score:
                    best_score, best_tgt, best_reason = score, tgt, reason

            if best_tgt and best_score >= self.TABLE_THRESHOLD:
                recs.append(Recommendation(
                    rec_type="table_match",
                    source_ref=src,
                    target_ref=best_tgt,
                    confidence=best_score,
                    reason=best_reason
                ))
                mapping[src] = best_tgt
                if best_score == 1.0:
                    matched.add(best_tgt)

        return recs, mapping

    def _recommend_columns(
        self,
        src_table: str,
        tgt_table: str,
        src_cols: Dict,
        tgt_cols: Dict
    ) -> List[Recommendation]:
        recs = []
        matched = set()

        for src_col, src_def in src_cols.items():
            best_tgt, best_score, best_reason = None, 0.0, ""

            for tgt_col, tgt_def in tgt_cols.items():
                if tgt_col in matched:
                    continue
                score, reason = self._score(src_col, tgt_col)
                if score > best_score:
                    best_score, best_tgt, best_reason = score, tgt_col, reason

            if best_tgt and best_score >= self.COLUMN_THRESHOLD:
                rec_type = "rename_candidate" if src_col != best_tgt else "column_match"
                recs.append(Recommendation(
                    rec_type=rec_type,
                    source_ref=f"{src_table}.{src_col}",
                    target_ref=f"{tgt_table}.{best_tgt}",
                    confidence=best_score,
                    reason=best_reason
                ))
                if best_score >= 0.90:
                    matched.add(best_tgt)

        return recs

    def _score(self, name1: str, name2: str) -> Tuple[float, str]:
        n1, n2 = name1.lower().strip(), name2.lower().strip()

        if n1 == n2:
            return 1.0, "exact_match"

        # Alias expansion
        def expand(n: str) -> str:
            parts = n.replace("-", "_").split("_")
            return "_".join(COMMON_ALIASES.get(p, p) for p in parts)

        e1, e2 = expand(n1), expand(n2)
        if e1 == e2:
            return 0.92, "alias_match"

        # Levenshtein on originals and expanded
        fuzzy_orig = levenshtein_similarity(n1, n2)
        fuzzy_exp  = levenshtein_similarity(e1, e2)

        # Prefix/suffix strip
        prefix_score = 0.0
        for a, b in [(n1, n2), (n2, n1)]:
            if b.endswith("_" + a) or b.startswith(a + "_"):
                prefix_score = 0.78
                break

        best = max(fuzzy_orig, fuzzy_exp, prefix_score)
        if best == fuzzy_orig:
            reason = f"fuzzy_{fuzzy_orig:.2f}"
        elif best == fuzzy_exp:
            reason = "alias_fuzzy"
        else:
            reason = "prefix_suffix"

        return best, reason

    def apply_accepted(
        self,
        recs: List[Recommendation],
        accepted_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Build table_mappings and column_mappings from accepted recommendation IDs.
        accepted_ids example: ["users→customers", "users.first_name→customers.fname"]
        """
        accepted_set = set(accepted_ids)
        table_map = {}
        col_map   = {}

        for r in recs:
            rid = f"{r.source_ref}→{r.target_ref}"
            if rid not in accepted_set:
                continue
            if r.rec_type == "table_match":
                table_map[r.source_ref] = r.target_ref
            elif r.rec_type in ("column_match", "rename_candidate"):
                src_tbl, src_col = r.source_ref.rsplit(".", 1)
                _,       tgt_col = r.target_ref.rsplit(".", 1)
                col_map.setdefault(src_tbl, {})[src_col] = tgt_col

        return {"table_mappings": table_map, "column_mappings": col_map}
