"""
Assessment Engine
File: migration/backend/intelligence_service/assessment/assessment_engine.py

Generates a comprehensive pre-migration assessment report.
Reads ONLY from Metadata Catalog and existing schema data.
Never connects to source/target databases directly.
Never modifies any migration state.

Report includes:
  - Complexity: LOW / MEDIUM / HIGH / CRITICAL
  - Risk level: low / medium / high / critical
  - Total tables, rows, size
  - Estimated duration
  - Recommended worker count
  - Recommended chunk strategy
  - Blocking issues (must fix before migration)
  - Warnings (should review)
  - Table-by-table breakdown
  - Specific recommendations

Complexity rules:
  LOW:      < 10 tables, < 10M rows, no LOB, no unsafe conversions
  MEDIUM:   10-50 tables OR 10M-1B rows OR LOB columns OR lossy conversions
  HIGH:     > 50 tables OR > 1B rows OR unsafe conversions OR broken FKs
  CRITICAL: > 500 tables OR > 100B rows OR circular FKs OR data quality errors
"""

import datetime
import uuid
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


# Thresholds
ROWS_MEDIUM    = 10_000_000
ROWS_HIGH      = 1_000_000_000
ROWS_CRITICAL  = 100_000_000_000
TABLES_MEDIUM  = 10
TABLES_HIGH    = 50
TABLES_CRITICAL = 500
SIZE_GB_LARGE  = 100
THROUGHPUT_RPS = 40_000   # conservative estimate rows/sec with 4 workers


@dataclass
class AssessmentReport:
    connection_id:       str
    complexity:          str
    risk_level:          str
    total_tables:        int
    total_rows:          int
    total_size_gb:       float
    estimated_duration:  str
    recommended_workers: int
    recommended_chunk_strategy: str
    blocking_issues:     List[Dict] = field(default_factory=list)
    warnings:            List[Dict] = field(default_factory=list)
    recommendations:     List[Dict] = field(default_factory=list)
    table_breakdown:     List[Dict] = field(default_factory=list)
    generated_at:        str = ""

    def to_dict(self) -> dict:
        return {
            "connection_id":           self.connection_id,
            "complexity":              self.complexity,
            "risk_level":              self.risk_level,
            "total_tables":            self.total_tables,
            "total_rows":              self.total_rows,
            "total_size_gb":           self.total_size_gb,
            "estimated_duration":      self.estimated_duration,
            "recommended_workers":     self.recommended_workers,
            "recommended_chunk_strategy": self.recommended_chunk_strategy,
            "blocking_issues":         self.blocking_issues,
            "warnings":                self.warnings,
            "recommendations":         self.recommendations,
            "table_breakdown":         self.table_breakdown,
            "generated_at":            self.generated_at,
            "summary": {
                "blocking_count":   len(self.blocking_issues),
                "warning_count":    len(self.warnings),
                "recommendation_count": len(self.recommendations),
                "can_proceed":      len(self.blocking_issues) == 0,
            }
        }


class AssessmentEngine:

    def assess(
        self,
        db:               Session,
        connection_id:    str,
        tenant_id:        str = "local",
        schema_version_id: Optional[str] = None,
        dry_run_result:   Optional[Dict] = None,  # from schema mapping service if available
    ) -> AssessmentReport:
        """
        Generate a complete assessment report for a source database.
        Reads from Metadata Catalog — requires Part 3 scan to have run first.
        """
        logger.info("Assessment starting", connection_id=connection_id)

        # ── Gather all catalog data ───────────────────────────────────────
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

        # Build per-table catalog dict
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

        if not catalog:
            return AssessmentReport(
                connection_id=connection_id,
                complexity="UNKNOWN",
                risk_level="unknown",
                total_tables=0,
                total_rows=0,
                total_size_gb=0,
                estimated_duration="unknown",
                recommended_workers=4,
                recommended_chunk_strategy="size_based",
                blocking_issues=[{
                    "type": "no_metadata",
                    "severity": "blocking",
                    "message": "No metadata found. Run POST /intelligence/scans first.",
                }],
                generated_at=datetime.datetime.utcnow().isoformat(),
            )

        # ── Aggregate totals ──────────────────────────────────────────────
        total_tables   = len(catalog)
        total_rows     = 0
        total_size_gb  = 0.0
        lob_tables     = []
        skewed_tables  = []
        orphan_tables  = []
        large_tables   = []
        high_growth    = []
        table_breakdown = []

        for tname, data in catalog.items():
            stats = data.get("statistics", {})
            rc    = stats.get("row_count", 0) or 0
            sg    = stats.get("size_gb", 0) or 0
            total_rows    += rc
            total_size_gb += sg

            lob   = data.get("lob_detection", {})
            dist  = data.get("distribution", {})
            rel   = data.get("relationship", {})
            gr    = data.get("growth_rate", {})

            has_lob = lob.get("has_lob", False)
            if has_lob:
                lob_tables.append(tname)

            skewed_cols = dist.get("skewed_columns", [])
            if skewed_cols:
                skewed_tables.append(tname)

            if isinstance(rel, dict) and rel.get("orphan_count", 0) > 0:
                orphan_tables.append(tname)

            if sg >= SIZE_GB_LARGE:
                large_tables.append(tname)

            rpm = gr.get("rows_per_month", 0) or 0
            if rc > 0 and rpm > rc * 0.1:
                high_growth.append(tname)

            table_breakdown.append({
                "table_name":    tname,
                "row_count":     rc,
                "size_gb":       sg,
                "has_lob":       has_lob,
                "lob_columns":   lob.get("lob_columns", []),
                "skewed_cols":   skewed_cols,
                "orphan_count":  rel.get("orphan_count", 0) if isinstance(rel, dict) else 0,
                "cardinality":   rel.get("cardinality", "unknown") if isinstance(rel, dict) else "unknown",
            })

        table_breakdown.sort(key=lambda x: x["size_gb"], reverse=True)

        # ── Determine complexity ──────────────────────────────────────────
        complexity = self._compute_complexity(
            total_tables, total_rows, lob_tables, orphan_tables, dry_run_result
        )

        # ── Determine risk level ──────────────────────────────────────────
        risk_level = self._compute_risk(
            complexity, orphan_tables, dry_run_result, large_tables
        )

        # ── Compute duration estimate ─────────────────────────────────────
        workers         = self._recommend_workers(total_rows, total_size_gb, complexity)
        effective_tput  = THROUGHPUT_RPS * workers
        est_seconds     = int(total_rows / effective_tput) if effective_tput > 0 else 0
        est_duration    = self._fmt_duration(est_seconds)

        # ── Chunk strategy ────────────────────────────────────────────────
        chunk_strategy = self._recommend_chunk_strategy(total_rows, lob_tables, large_tables)

        # ── Build blocking issues ─────────────────────────────────────────
        blocking = []
        if orphan_tables:
            blocking.append({
                "type":     "orphan_fk_rows",
                "severity": "blocking",
                "tables":   orphan_tables,
                "message":  f"{len(orphan_tables)} table(s) have FK rows with no matching parent. "
                            "These will fail FK constraint checks on target.",
                "fix":      "Run Data Quality Scanner to identify and remediate orphan rows before migration.",
            })

        if dry_run_result:
            unsafe = dry_run_result.get("unsafe_conversions", [])
            if unsafe:
                blocking.append({
                    "type":     "unsafe_type_conversions",
                    "severity": "blocking",
                    "count":    len(unsafe),
                    "examples": unsafe[:3],
                    "message":  f"{len(unsafe)} unsafe type conversion(s) detected.",
                    "fix":      "Generate manual migration scripts for affected tables.",
                })

        # ── Build warnings ────────────────────────────────────────────────
        warnings = []
        if lob_tables:
            warnings.append({
                "type":    "lob_columns",
                "tables":  lob_tables,
                "message": f"{len(lob_tables)} table(s) contain LOB columns "
                           "(BLOB/TEXT/BYTEA). These significantly increase migration time.",
                "fix":     "Reduce chunk size for LOB tables. Consider streaming mode.",
            })

        if skewed_tables:
            warnings.append({
                "type":    "skewed_data",
                "tables":  skewed_tables,
                "message": f"{len(skewed_tables)} table(s) have highly skewed column "
                           "distributions. PK-range chunking may produce uneven chunk sizes.",
                "fix":     "Use size_based chunk strategy for these tables.",
            })

        if large_tables:
            warnings.append({
                "type":    "large_tables",
                "tables":  large_tables,
                "message": f"{len(large_tables)} table(s) exceed 100 GB. "
                           "These will dominate total migration time.",
                "fix":     f"Allocate {workers} workers minimum. Monitor throughput closely.",
            })

        if high_growth:
            warnings.append({
                "type":    "high_growth_tables",
                "tables":  high_growth,
                "message": f"{len(high_growth)} table(s) are growing >10%/month. "
                           "Consider CDC mode to capture changes during migration.",
            })

        if dry_run_result:
            lossy = dry_run_result.get("lossy_conversions", [])
            if lossy:
                warnings.append({
                    "type":    "lossy_conversions",
                    "count":   len(lossy),
                    "message": f"{len(lossy)} lossy type conversion(s). Precision may be lost.",
                    "fix":     "Review and validate each lossy conversion before proceeding.",
                })

        # ── Build recommendations ─────────────────────────────────────────
        recommendations = self._build_recommendations(
            total_rows, total_size_gb, lob_tables, large_tables,
            high_growth, workers, chunk_strategy, complexity
        )

        report = AssessmentReport(
            connection_id=connection_id,
            complexity=complexity,
            risk_level=risk_level,
            total_tables=total_tables,
            total_rows=total_rows,
            total_size_gb=round(total_size_gb, 2),
            estimated_duration=est_duration,
            recommended_workers=workers,
            recommended_chunk_strategy=chunk_strategy,
            blocking_issues=blocking,
            warnings=warnings,
            recommendations=recommendations,
            table_breakdown=table_breakdown[:50],  # top 50 tables
            generated_at=datetime.datetime.utcnow().isoformat(),
        )

        # ── Persist ───────────────────────────────────────────────────────
        self._save_report(db, report, tenant_id, schema_version_id)

        logger.info("Assessment complete",
                    connection_id=connection_id,
                    complexity=complexity,
                    tables=total_tables,
                    rows=total_rows,
                    blocking=len(blocking))

        return report

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_complexity(self, tables, rows, lob_tables, orphan_tables, dry_run) -> str:
        unsafe_count = len((dry_run or {}).get("unsafe_conversions", []))

        if tables >= TABLES_CRITICAL or rows >= ROWS_CRITICAL:
            return "CRITICAL"
        if (tables >= TABLES_HIGH or rows >= ROWS_HIGH or
                unsafe_count > 0 or len(orphan_tables) > 5):
            return "HIGH"
        if (tables >= TABLES_MEDIUM or rows >= ROWS_MEDIUM or
                lob_tables or len(orphan_tables) > 0):
            return "MEDIUM"
        return "LOW"

    def _compute_risk(self, complexity, orphan_tables, dry_run, large_tables) -> str:
        unsafe = len((dry_run or {}).get("unsafe_conversions", []))
        if complexity == "CRITICAL" or (unsafe > 0 and len(orphan_tables) > 0):
            return "critical"
        if complexity == "HIGH" or unsafe > 0 or len(orphan_tables) > 3:
            return "high"
        if complexity == "MEDIUM" or len(orphan_tables) > 0 or large_tables:
            return "medium"
        return "low"

    def _recommend_workers(self, rows, size_gb, complexity) -> int:
        if rows >= ROWS_CRITICAL or size_gb >= 10000:
            return 32
        if rows >= ROWS_HIGH or size_gb >= 1000:
            return 16
        if rows >= ROWS_MEDIUM or size_gb >= 100:
            return 8
        if complexity in ("MEDIUM", "HIGH"):
            return 4
        return 2

    def _recommend_chunk_strategy(self, rows, lob_tables, large_tables) -> str:
        if rows >= ROWS_HIGH:
            return "streaming"
        if lob_tables:
            return "count_based"
        if large_tables:
            return "size_based"
        return "size_based"

    def _build_recommendations(self, rows, size_gb, lob_tables, large_tables,
                                high_growth, workers, chunk_strategy, complexity) -> List[Dict]:
        recs = []

        if rows > 0:
            recs.append({
                "priority": "high",
                "category": "workers",
                "message":  f"Use {workers} workers for optimal throughput.",
                "detail":   f"Estimated {self._fmt_duration(int(rows / (THROUGHPUT_RPS * workers)))} with {workers} workers.",
            })

        recs.append({
            "priority": "high",
            "category": "chunk_strategy",
            "message":  f"Use '{chunk_strategy}' chunk strategy.",
            "detail":   "Set in AdaptiveChunkPlanner or override per-table in migration settings.",
        })

        if high_growth:
            recs.append({
                "priority": "high",
                "category": "cdc",
                "message":  "Enable CDC (Change Data Capture) for near-zero downtime.",
                "detail":   f"{len(high_growth)} high-growth table(s) detected. "
                            "Without CDC, target will be out of date by migration end.",
            })

        if size_gb >= 1000:
            recs.append({
                "priority": "medium",
                "category": "scheduling",
                "message":  "Schedule migration during lowest-traffic hours.",
                "detail":   f"At {size_gb:.0f} GB, migration will take significant time. "
                            "Validate your maintenance window is long enough.",
            })

        if lob_tables:
            recs.append({
                "priority": "medium",
                "category": "lob",
                "message":  f"Reduce chunk size for LOB tables: {lob_tables[:3]}",
                "detail":   "LOB columns increase memory pressure. "
                            "Use count_based strategy with max 1,000 rows/chunk for these tables.",
            })

        if complexity in ("HIGH", "CRITICAL"):
            recs.append({
                "priority": "high",
                "category": "rollback",
                "message":  "Generate and test rollback plan before executing migration.",
                "detail":   "High complexity migrations have higher rollback probability. "
                            "Use POST /jobs/{id}/rollback/generate before starting.",
            })

        return recs

    def _fmt_duration(self, seconds: int) -> str:
        if seconds <= 0:    return "< 1 minute"
        if seconds < 60:    return f"{seconds}s"
        if seconds < 3600:  m, s = divmod(seconds, 60);  return f"{m}m {s}s"
        if seconds < 86400: h, r = divmod(seconds, 3600); return f"{h}h {r//60}m"
        d, r = divmod(seconds, 86400); return f"{d}d {r//3600}h"

    def _save_report(self, db, report: AssessmentReport, tenant_id, schema_version_id):
        try:
            d = report.to_dict()
            db.execute(
                text("""
                    INSERT INTO assessment_reports
                        (id, tenant_id, connection_id, schema_version_id,
                         complexity, risk_level, total_tables, total_rows,
                         total_size_gb, estimated_duration,
                         recommended_workers, recommended_chunk_strategy,
                         blocking_issues, warnings, recommendations,
                         table_breakdown, full_report, generated_at)
                    VALUES
                        (gen_random_uuid(), :tid, :cid, :svid,
                         :complexity, :risk, :tables, :rows,
                         :size_gb, :duration,
                         :workers, :chunk_strat,
                         :blocking::jsonb, :warnings::jsonb, :recs::jsonb,
                         :breakdown::jsonb, :full::jsonb, :now)
                """),
                {
                    "tid":        tenant_id,
                    "cid":        report.connection_id,
                    "svid":       schema_version_id,
                    "complexity": report.complexity,
                    "risk":       report.risk_level,
                    "tables":     report.total_tables,
                    "rows":       report.total_rows,
                    "size_gb":    report.total_size_gb,
                    "duration":   report.estimated_duration,
                    "workers":    report.recommended_workers,
                    "chunk_strat":report.recommended_chunk_strategy,
                    "blocking":   json.dumps(report.blocking_issues),
                    "warnings":   json.dumps(report.warnings),
                    "recs":       json.dumps(report.recommendations),
                    "breakdown":  json.dumps(report.table_breakdown[:20]),
                    "full":       json.dumps(d),
                    "now":        datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to save assessment report", error=str(e))
            try:
                db.rollback()
            except Exception:
                pass
