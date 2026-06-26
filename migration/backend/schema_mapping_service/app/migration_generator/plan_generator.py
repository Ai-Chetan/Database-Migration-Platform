"""
Migration Plan Generator + Dry Run
File: migration/backend/schema_mapping_service/app/migration_generator/plan_generator.py

Before any data moves:
  - Dry Run: analyze risk, estimate duration, list unsafe conversions
  - Plan:    ordered execution steps with FK-aware table ordering
"""

import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from backend.schema_mapping_service.app.comparison.schema_comparator import (
    conversion_safety, get_base_type
)

THROUGHPUT_RPS = {
    "mysql_to_mysql": 50_000,
    "mysql_to_postgresql": 30_000,
    "postgresql_to_postgresql": 60_000,
    "default": 40_000,
}


def _fmt(seconds: float) -> str:
    if seconds <= 0:
        return "< 1 minute"
    s = int(seconds)
    if s < 60:       return f"{s}s"
    if s < 3600:     m, r = divmod(s,60);  return f"{m}m {r}s"
    if s < 86400:    h, r = divmod(s,3600); return f"{h}h {r//60}m"
    d, r = divmod(s, 86400); return f"{d}d {r//3600}h"


@dataclass
class DryRunResult:
    project_id:         str
    complexity:         str           # LOW | MEDIUM | HIGH
    tables_total:       int
    tables_automatable: int
    tables_manual_only: int
    total_rows:         int
    estimated_duration: str
    unsafe_conversions: List[Dict]
    lossy_conversions:  List[Dict]
    missing_mappings:   List[str]
    risk_factors:       List[str]
    recommendations:    List[str]
    generated_at:       str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MigrationStep:
    step_number:    int
    step_type:      str     # create_tables|migrate_data|rebuild_indexes|validate|generate_scripts
    description:    str
    tables:         List[str] = field(default_factory=list)
    is_manual:      bool = False
    estimated_rows: int = 0
    order_reason:   str = ""


@dataclass
class MigrationPlan:
    project_id:         str
    steps:              List[MigrationStep]
    total_steps:        int
    auto_tables:        List[str]
    manual_tables:      List[str]
    total_rows:         int
    estimated_duration: str
    generated_at:       str

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "total_steps": self.total_steps,
            "auto_tables": self.auto_tables,
            "manual_tables": self.manual_tables,
            "total_rows": self.total_rows,
            "estimated_duration": self.estimated_duration,
            "generated_at": self.generated_at,
            "steps": [
                {
                    "step_number": s.step_number, "step_type": s.step_type,
                    "description": s.description, "tables": s.tables,
                    "is_manual": s.is_manual, "estimated_rows": s.estimated_rows,
                    "order_reason": s.order_reason,
                }
                for s in self.steps
            ],
        }


class MigrationPlanGenerator:

    def dry_run(
        self,
        project_id: str,
        source_schema: Dict[str, Any],
        target_schema: Dict[str, Any],
        table_mappings: Dict[str, Any],
    ) -> DryRunResult:
        src_tables     = source_schema.get("tables", {})
        tgt_tables     = target_schema.get("tables", {})
        unsafe_convs   = []
        lossy_convs    = []
        manual_tables  = []
        auto_tables    = []
        missing_maps   = []
        total_rows     = 0

        for tname, tdata in src_tables.items():
            total_rows += tdata.get("row_count", 0)
            mapping = table_mappings.get(tname)
            if not mapping:
                missing_maps.append(tname)
                continue

            has_unsafe  = False
            src_cols    = tdata.get("columns", {})
            col_maps    = mapping.get("column_mappings", {})
            tgt_tname   = mapping.get("new_table_name_schema") or mapping.get("target", tname)

            for src_col, tgt_col in col_maps.items():
                src_type = src_cols.get(src_col, {}).get("type", "")
                tgt_type = tgt_tables.get(tgt_tname, {}).get("columns", {}).get(tgt_col, {}).get("type", "")
                if not src_type or not tgt_type:
                    continue
                safety = conversion_safety(src_type, tgt_type)
                entry  = {"table": tname, "source_column": src_col, "target_column": tgt_col,
                          "from_type": src_type, "to_type": tgt_type}
                if safety == "unsafe":
                    has_unsafe = True
                    unsafe_convs.append(entry)
                elif safety == "lossy":
                    lossy_convs.append(entry)

            (manual_tables if has_unsafe else auto_tables).append(tname)

        risk_factors = []
        if unsafe_convs:
            risk_factors.append(f"{len(unsafe_convs)} unsafe type conversion(s) require manual scripts")
        if lossy_convs:
            risk_factors.append(f"{len(lossy_convs)} lossy conversion(s) may cause data truncation")
        if missing_maps:
            risk_factors.append(f"{len(missing_maps)} table(s) have no mapping: {', '.join(missing_maps[:5])}")
        if total_rows > 100_000_000:
            risk_factors.append("Dataset >100M rows — migration will take several hours")

        complexity = "HIGH" if (unsafe_convs or len(missing_maps) > 3) else (
                     "MEDIUM" if (lossy_convs or manual_tables) else "LOW")

        throughput     = THROUGHPUT_RPS["default"]
        eta_seconds    = total_rows / throughput if throughput > 0 else 0

        recs = []
        if unsafe_convs:
            recs.append("Use 'Generate Script' for tables with unsafe conversions")
        if lossy_convs:
            recs.append("Review lossy conversions — precision/data may be lost")
        if total_rows > 10_000_000:
            recs.append("Use 4+ workers to improve throughput")
        if not risk_factors:
            recs.append("No major risks detected. Migration can proceed automatically.")

        return DryRunResult(
            project_id=project_id,
            complexity=complexity,
            tables_total=len(src_tables),
            tables_automatable=len(auto_tables),
            tables_manual_only=len(manual_tables),
            total_rows=total_rows,
            estimated_duration=_fmt(eta_seconds),
            unsafe_conversions=unsafe_convs,
            lossy_conversions=lossy_convs,
            missing_mappings=missing_maps,
            risk_factors=risk_factors,
            recommendations=recs,
            generated_at=datetime.datetime.utcnow().isoformat(),
        )

    def generate_plan(
        self,
        project_id: str,
        source_schema: Dict[str, Any],
        table_mappings: Dict[str, Any],
        dry_run: DryRunResult,
    ) -> MigrationPlan:
        src_tables    = source_schema.get("tables", {})
        auto_tables   = [t for t in src_tables
                         if t not in dry_run.tables_manual_only
                         and t not in dry_run.missing_mappings]
        manual_tables = dry_run.tables_manual_only

        ordered     = self._topo_sort(auto_tables, source_schema)
        no_fk       = [t for t in ordered if not src_tables.get(t, {}).get("foreign_keys")]
        fk_dep      = [t for t in ordered if t not in no_fk]

        steps: List[MigrationStep] = []
        n = 1

        steps.append(MigrationStep(n, "create_tables",
            f"Create {len(auto_tables)+len(manual_tables)} target tables (without indexes)",
            tables=auto_tables + manual_tables,
            order_reason="Schema first, data second, indexes last")); n += 1

        if no_fk:
            rows = sum(src_tables.get(t, {}).get("row_count", 0) for t in no_fk)
            steps.append(MigrationStep(n, "migrate_data",
                f"Migrate {len(no_fk)} independent tables (no FK constraints)",
                tables=no_fk, estimated_rows=rows,
                order_reason="Independent tables first — no dependency conflicts")); n += 1

        if fk_dep:
            rows = sum(src_tables.get(t, {}).get("row_count", 0) for t in fk_dep)
            steps.append(MigrationStep(n, "migrate_data",
                f"Migrate {len(fk_dep)} FK-dependent tables in dependency order",
                tables=fk_dep, estimated_rows=rows,
                order_reason="FK order ensures referenced data exists first")); n += 1

        steps.append(MigrationStep(n, "rebuild_indexes",
            "Rebuild all indexes and constraints on target tables",
            tables=auto_tables,
            order_reason="Indexes after bulk load = significantly faster")); n += 1

        steps.append(MigrationStep(n, "validate",
            "Validate row counts and checksums for all migrated tables",
            tables=auto_tables)); n += 1

        if manual_tables:
            steps.append(MigrationStep(n, "generate_scripts",
                f"Generate manual scripts for {len(manual_tables)} table(s) with unsafe conversions",
                tables=manual_tables, is_manual=True,
                order_reason="These require human review and custom logic")); n += 1

        total_rows = sum(src_tables.get(t, {}).get("row_count", 0) for t in auto_tables)

        return MigrationPlan(
            project_id=project_id,
            steps=steps,
            total_steps=len(steps),
            auto_tables=auto_tables,
            manual_tables=manual_tables,
            total_rows=total_rows,
            estimated_duration=dry_run.estimated_duration,
            generated_at=datetime.datetime.utcnow().isoformat(),
        )

    def _topo_sort(self, tables: List[str], source_schema: Dict) -> List[str]:
        table_set = set(tables)
        adj = {t: set() for t in tables}
        for t in tables:
            for fk in source_schema.get("tables", {}).get(t, {}).get("foreign_keys", []):
                ref = fk.get("ref_table")
                if ref in table_set and ref != t:
                    adj[t].add(ref)
        in_deg = {t: len(d) for t, d in adj.items()}
        queue  = [t for t, d in in_deg.items() if d == 0]
        result = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for t in tables:
                if node in adj[t]:
                    adj[t].remove(node)
                    in_deg[t] -= 1
                    if in_deg[t] == 0:
                        queue.append(t)
        for t in tables:
            if t not in result:
                result.append(t)
        return result
