"""
Intelligence Service Router
File: migration/backend/intelligence_service/routers/intelligence_service.py

Endpoints:
    POST /assess                        → run assessment report
    GET  /assess/{report_id}            → get saved report
    GET  /assess/latest                 → latest report for a connection
    POST /advise                        → run migration advisor
    POST /estimate                      → estimate cost and duration
    POST /quality/scan                  → scan one table for data quality issues
    POST /quality/scan-all              → scan all tables
    GET  /quality/results               → get saved quality scan results
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db
from backend.intelligence_service.assessment.assessment_engine import AssessmentEngine
from backend.intelligence_service.advisor.migration_advisor import MigrationAdvisor
from backend.intelligence_service.estimator.cost_estimator import CostEstimator
from backend.intelligence_service.scanner.data_quality_scanner import DataQualityScanner

router    = APIRouter(tags=["Intelligence Service"])
assessor  = AssessmentEngine()
advisor   = MigrationAdvisor()
estimator = CostEstimator()
scanner   = DataQualityScanner()


# ── Request models ─────────────────────────────────────────────────────────────

class AssessRequest(BaseModel):
    connection_id:     str
    tenant_id:         str = "local"
    schema_version_id: Optional[str] = None
    dry_run_result:    Optional[Dict[str, Any]] = None


class AdviseRequest(BaseModel):
    connection_id:  str
    tenant_id:      str = "local"
    schema_info:    Optional[Dict[str, Any]] = None
    dry_run_result: Optional[Dict[str, Any]] = None


class EstimateRequest(BaseModel):
    connection_id:  str
    tenant_id:      str = "local"
    source_engine:  str = "mysql"
    target_engine:  str = "mysql"
    workers:        Optional[int] = None
    cloud_provider: str = "aws"
    custom_pricing: Optional[Dict[str, float]] = None


class QualityScanRequest(BaseModel):
    connection_id: str
    source_config: Dict[str, Any]
    schema_info:   Dict[str, Any]
    tenant_id:     str = "local"
    checks:        Optional[List[str]] = None


class QualityScanTableRequest(BaseModel):
    connection_id: str
    source_config: Dict[str, Any]
    table_name:    str
    schema_info:   Dict[str, Any]
    tenant_id:     str = "local"
    checks:        Optional[List[str]] = None


# ── Assessment endpoints ───────────────────────────────────────────────────────

@router.post("/assess", summary="Generate pre-migration assessment report")
def run_assessment(req: AssessRequest, db: Session = Depends(get_db)):
    """
    Generates a comprehensive pre-migration assessment using Metadata Catalog data.
    Requires POST /intelligence/scans to have completed first (Part 3).

    Report includes:
      - Complexity: LOW | MEDIUM | HIGH | CRITICAL
      - Risk level: low | medium | high | critical
      - Total tables, rows, estimated size
      - Estimated duration with recommended worker count
      - Blocking issues that MUST be fixed before migration
      - Warnings that SHOULD be reviewed
      - Prioritized recommendations
      - Per-table breakdown (top 50 by size)

    Complexity rules:
      LOW:      < 10 tables, < 10M rows, no LOB, no conversions
      MEDIUM:   10-50 tables OR 10M-1B rows OR LOB columns
      HIGH:     > 50 tables OR > 1B rows OR unsafe conversions OR broken FKs
      CRITICAL: > 500 tables OR > 100B rows OR data quality errors

    This service ONLY reads — it never modifies migration state.
    """
    report = assessor.assess(
        db=db,
        connection_id=req.connection_id,
        tenant_id=req.tenant_id,
        schema_version_id=req.schema_version_id,
        dry_run_result=req.dry_run_result,
    )
    return report.to_dict()


@router.get("/assess/reports", summary="List assessment reports for a connection")
def list_reports(
    connection_id: str,
    tenant_id:     str = "local",
    limit:         int = 10,
    db:            Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT id, complexity, risk_level, total_tables, total_rows,
                   total_size_gb, estimated_duration, recommended_workers,
                   generated_at
            FROM assessment_reports
            WHERE connection_id=:cid AND tenant_id=:tid
            ORDER BY generated_at DESC LIMIT :lim
        """),
        {"cid": connection_id, "tid": tenant_id, "lim": limit}
    ).fetchall()

    reports = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        reports.append(d)
    return {"reports": reports, "total": len(reports)}


@router.get("/assess/reports/{report_id}", summary="Get a saved assessment report")
def get_report(report_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT full_report FROM assessment_reports WHERE id=:id"),
        {"id": report_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    data = row[0]
    if isinstance(data, str):
        import json
        data = json.loads(data)
    return data


# ── Advisor endpoints ──────────────────────────────────────────────────────────

@router.post("/advise", summary="Get data-aware migration advice")
def run_advisor(req: AdviseRequest, db: Session = Depends(get_db)):
    """
    Generates prioritized, data-aware migration advice.
    Unlike the basic recommendation engine (name-based matching),
    the Advisor reasons about ACTUAL DATA from the Metadata Catalog:

    Examples of data-aware advice:
      - "BIGINT → INT: max value is 3,200,000,000 — exceeds INT max. Will fail."
      - "orders.customer_id: avg 847 children/parent (high fan-out). Use CDC."
      - "users.email: PII detected by value pattern. Configure masking before migration."
      - "orders table: missing index on FK column customer_id. Queries will be slow."

    Advice categories:
      type_conversion  — unsafe/lossy conversions with data evidence
      cdc              — whether CDC is recommended based on growth rate/fan-out
      chunk            — per-table chunk size advice based on LOB/skew
      masking          — PII detection (column names + value patterns)
      index            — missing indexes on FK columns

    Requires POST /intelligence/scans to have run first (Part 3).
    Optionally accepts dry_run_result from Schema Mapping Service (port 8003)
    to combine schema-level and data-level advice.
    """
    result = advisor.advise(
        db=db,
        connection_id=req.connection_id,
        schema_info=req.schema_info,
        dry_run_result=req.dry_run_result,
        tenant_id=req.tenant_id,
    )
    return result


# ── Cost estimator endpoints ───────────────────────────────────────────────────

@router.post("/estimate", summary="Estimate migration cost and duration")
def run_estimate(req: EstimateRequest, db: Session = Depends(get_db)):
    """
    Estimates migration cost and duration using REAL table statistics.

    cloud_provider options: aws | gcp | azure
    Or supply custom_pricing:
    {
      "compute_per_hour_per_worker": 0.10,
      "storage_per_gb_month": 0.023,
      "network_egress_per_gb": 0.09,
      "name": "My Cloud"
    }

    Response includes:
    {
      "summary": {
        "estimated_duration": "9h 23m",
        "total_rows":         4_200_000_000,
        "total_size_gb":      5100.0,
        "target_storage_gb":  6375.0,
        "recommended_workers": 16
      },
      "cost_breakdown": {
        "compute_usd": 147.20,
        "storage_usd": 152.25,
        "network_usd": 459.00,
        "total_usd":   758.45
      },
      "per_table": [...top 20 tables by size with per-table estimates...],
      "assumptions": [...]
    }

    Requires POST /intelligence/scans to have run first (Part 3).
    """
    result = estimator.estimate(
        db=db,
        connection_id=req.connection_id,
        source_engine=req.source_engine,
        target_engine=req.target_engine,
        workers=req.workers,
        cloud_provider=req.cloud_provider,
        custom_pricing=req.custom_pricing,
        tenant_id=req.tenant_id,
    )
    return result.to_dict()


# ── Data Quality Scanner endpoints ────────────────────────────────────────────

@router.post("/quality/scan-table", summary="Scan one table for data quality issues")
def scan_table_quality(req: QualityScanTableRequest, db: Session = Depends(get_db)):
    """
    Runs data quality checks on one specific table.

    Available checks (pass in 'checks' list, or omit for all):
      duplicate_pk       — duplicate PK values (blocks migration)
      null_pk            — NULL PK values (blocks migration)
      broken_fk          — FK rows with no parent (may cause FK violations)
      null_required      — NULL in NOT NULL columns (blocks migration)
      oversized_value    — values exceeding column length (blocks migration)
      invalid_date       — MySQL zero-dates '0000-00-00' (rejected by PostgreSQL)
      duplicate_unique   — duplicate values in UNIQUE columns (blocks migration)

    Severity:
      error   → will cause migration failure (fix before migrating)
      warning → may cause issues (review before migrating)
      info    → informational

    Results are saved to data_quality_results table.
    """
    issues = scanner.scan_table(
        db=db,
        connection_id=req.connection_id,
        source_config=req.source_config,
        table_name=req.table_name,
        schema_info=req.schema_info,
        tenant_id=req.tenant_id,
        checks=req.checks,
    )
    errors   = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    return {
        "table_name":   req.table_name,
        "total_issues": len(issues),
        "error_count":  errors,
        "warning_count": warnings,
        "can_proceed":  errors == 0,
        "issues":       [i.to_dict() for i in issues],
    }


@router.post("/quality/scan-all", summary="Scan all tables for data quality issues")
def scan_all_quality(req: QualityScanRequest, db: Session = Depends(get_db)):
    """
    Runs data quality checks across all tables in the schema.
    Returns aggregated summary plus per-table and per-issue detail.

    can_proceed=false means there are error-severity issues that WILL
    cause migration failures. Fix these before starting migration.
    """
    result = scanner.scan_all(
        db=db,
        connection_id=req.connection_id,
        source_config=req.source_config,
        schema_info=req.schema_info,
        tenant_id=req.tenant_id,
        checks=req.checks,
    )
    return result


@router.get("/quality/results", summary="Get saved data quality scan results")
def get_quality_results(
    connection_id: str,
    table_name:    Optional[str] = None,
    severity:      Optional[str] = None,
    tenant_id:     str = "local",
    limit:         int = 200,
    db:            Session = Depends(get_db),
):
    """Returns saved data quality scan results, filterable by table or severity."""
    conditions = ["connection_id=:cid"]
    params: Dict[str, Any] = {"cid": connection_id, "lim": limit}

    if table_name:
        conditions.append("table_name=:tname")
        params["tname"] = table_name
    if severity:
        conditions.append("severity=:sev")
        params["sev"] = severity

    where = " AND ".join(conditions)
    rows  = db.execute(
        text(f"""
            SELECT id, table_name, check_type, severity, affected_count,
                   affected_pct, details, recommendation, sample_values, scanned_at
            FROM data_quality_results
            WHERE {where}
            ORDER BY
                CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                affected_count DESC
            LIMIT :lim
        """),
        params
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        results.append(d)

    errors   = sum(1 for r in results if r["severity"] == "error")
    warnings = sum(1 for r in results if r["severity"] == "warning")

    return {
        "connection_id": connection_id,
        "total":         len(results),
        "error_count":   errors,
        "warning_count": warnings,
        "can_proceed":   errors == 0,
        "results":       results,
    }
