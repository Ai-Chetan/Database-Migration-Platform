"""
Report Generator
File: migration/backend/reporting/generators/report_generator.py

Generates structured migration reports from completed job data.
Reports are stored in migration_reports table and can be exported as JSON.

Report types:
    migration_summary   → overall job outcome, duration, rows, validation status
    validation_report   → per-table/chunk validation results
    performance_report  → throughput, chunk timing, worker efficiency
    audit_report        → all operator actions and approvals for a job
    data_quality_report → pre-migration quality scan findings
    compliance_report   → audit trail for GDPR/HIPAA/SOC2 compliance

Each report:
    - Reads from existing DB tables (no recalculation)
    - Stored as JSONB in migration_reports
    - Includes metadata: generated_by, generated_at, job context
"""

import datetime
import uuid
import json
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


class ReportGenerator:

    def generate(
        self,
        db:          Session,
        job_id:      str,
        report_type: str,
        generated_by: str = "system",
        tenant_id:   str = "local",
    ) -> Dict[str, Any]:
        """
        Generate a report for a migration job.
        Stores result in migration_reports and returns the report dict.
        """
        valid_types = {
            "migration_summary", "validation_report", "performance_report",
            "audit_report", "data_quality_report", "compliance_report",
        }
        if report_type not in valid_types:
            return {"error": f"Invalid report_type. Must be one of: {sorted(valid_types)}"}

        generators = {
            "migration_summary":   self._migration_summary,
            "validation_report":   self._validation_report,
            "performance_report":  self._performance_report,
            "audit_report":        self._audit_report,
            "data_quality_report": self._data_quality_report,
            "compliance_report":   self._compliance_report,
        }

        try:
            content = generators[report_type](db, job_id)
            title   = f"{report_type.replace('_', ' ').title()} — Job {job_id[:8]}..."

            report_id = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO migration_reports
                        (id, tenant_id, job_id, report_type, format,
                         title, content, generated_by, generated_at)
                    VALUES
                        (:id, :tid, :jid, :rtype, 'json',
                         :title, :content::jsonb, :by, :now)
                """),
                {
                    "id":      report_id,
                    "tid":     tenant_id,
                    "jid":     job_id,
                    "rtype":   report_type,
                    "title":   title,
                    "content": json.dumps(content, default=str),
                    "by":      generated_by,
                    "now":     datetime.datetime.utcnow(),
                }
            )
            db.commit()

            logger.info("Report generated",
                        report_type=report_type, job_id=job_id, report_id=report_id)
            return {
                "report_id":    report_id,
                "report_type":  report_type,
                "title":        title,
                "generated_at": datetime.datetime.utcnow().isoformat(),
                "content":      content,
            }

        except Exception as e:
            logger.error("Report generation failed",
                         report_type=report_type, job_id=job_id, error=str(e))
            return {"error": str(e), "report_type": report_type, "job_id": job_id}

    # ── Report generators ─────────────────────────────────────────────────────

    def _migration_summary(self, db: Session, job_id: str) -> Dict[str, Any]:
        """Overall migration outcome report."""
        job = db.execute(
            text("SELECT * FROM migration_jobs WHERE id=:id"), {"id": job_id}
        ).fetchone()
        if not job:
            return {"error": "Job not found"}

        j = self._to_dict(job)

        # Chunk stats
        chunk_stats = db.execute(
            text("""
                SELECT
                    COUNT(*)                                        AS total,
                    COUNT(*) FILTER (WHERE status='completed')     AS completed,
                    COUNT(*) FILTER (WHERE status='failed')        AS failed,
                    COUNT(*) FILTER (WHERE status='skipped')       AS skipped,
                    SUM(rows_processed)                            AS total_rows,
                    AVG(duration_ms) FILTER (WHERE duration_ms>0)  AS avg_chunk_ms,
                    MAX(duration_ms)                               AS max_chunk_ms,
                    MIN(duration_ms) FILTER (WHERE duration_ms>0)  AS min_chunk_ms
                FROM migration_chunks WHERE job_id=:jid
            """),
            {"jid": job_id}
        ).fetchone()
        cs = self._to_dict(chunk_stats)

        # Table stats
        table_stats = db.execute(
            text("""
                SELECT table_name, status, total_chunks, completed_chunks,
                       total_rows, migration_order
                FROM migration_tables WHERE job_id=:jid ORDER BY migration_order
            """),
            {"jid": job_id}
        ).fetchall()

        # Duration
        started    = j.get("started_at")
        completed  = j.get("completed_at")
        duration_s = None
        if started and completed:
            try:
                s = datetime.datetime.fromisoformat(started) if isinstance(started, str) else started
                c = datetime.datetime.fromisoformat(completed) if isinstance(completed, str) else completed
                duration_s = int((c - s).total_seconds())
            except Exception:
                pass

        return {
            "job_id":      job_id,
            "status":      j.get("status"),
            "source":      j.get("source_engine", "unknown"),
            "target":      j.get("target_engine", "unknown"),
            "started_at":  started,
            "completed_at": completed,
            "duration_seconds": duration_s,
            "duration_str": self._fmt(duration_s) if duration_s else "unknown",
            "tables":      len(table_stats),
            "chunks": {
                "total":     int(cs.get("total") or 0),
                "completed": int(cs.get("completed") or 0),
                "failed":    int(cs.get("failed") or 0),
                "skipped":   int(cs.get("skipped") or 0),
            },
            "rows_migrated":    int(cs.get("total_rows") or 0),
            "avg_chunk_ms":     round(float(cs.get("avg_chunk_ms") or 0)),
            "success_rate_pct": round(int(cs.get("completed") or 0) /
                                      max(int(cs.get("total") or 1), 1) * 100, 2),
            "table_breakdown":  [self._to_dict(t) for t in table_stats],
        }

    def _validation_report(self, db: Session, job_id: str) -> Dict[str, Any]:
        """Per-chunk validation results."""
        chunks = db.execute(
            text("""
                SELECT mc.id, mt.table_name, mc.pk_start, mc.pk_end,
                       mc.validation_status, mc.source_row_count, mc.target_row_count,
                       mc.checksum, mc.status
                FROM migration_chunks mc
                JOIN migration_tables mt ON mc.table_id = mt.id
                WHERE mc.job_id=:jid
                ORDER BY mt.table_name, mc.pk_start
            """),
            {"jid": job_id}
        ).fetchall()

        passed  = sum(1 for c in chunks if self._to_dict(c).get("validation_status") == "passed")
        failed  = sum(1 for c in chunks if self._to_dict(c).get("validation_status") == "failed")
        pending = sum(1 for c in chunks if self._to_dict(c).get("validation_status") == "pending")

        return {
            "job_id":        job_id,
            "total_chunks":  len(chunks),
            "validation_passed":  passed,
            "validation_failed":  failed,
            "validation_pending": pending,
            "overall_passed": failed == 0,
            "chunks": [self._to_dict(c) for c in chunks],
        }

    def _performance_report(self, db: Session, job_id: str) -> Dict[str, Any]:
        """Throughput and timing report."""
        # Per-table performance
        table_perf = db.execute(
            text("""
                SELECT mt.table_name,
                       COUNT(mc.id)              AS chunks,
                       SUM(mc.rows_processed)    AS rows,
                       SUM(mc.duration_ms)       AS total_ms,
                       AVG(mc.duration_ms) FILTER (WHERE mc.duration_ms>0) AS avg_ms,
                       CASE WHEN SUM(mc.duration_ms) > 0 THEN
                           SUM(mc.rows_processed)::float / SUM(mc.duration_ms) * 1000
                       ELSE 0 END                AS rps
                FROM migration_tables mt
                JOIN migration_chunks mc ON mc.table_id = mt.id
                WHERE mt.job_id=:jid AND mc.status='completed'
                GROUP BY mt.table_name
                ORDER BY rows DESC
            """),
            {"jid": job_id}
        ).fetchall()

        # Benchmark comparison
        benchmark = db.execute(
            text("""
                SELECT avg_rows_per_sec, peak_rows_per_sec, worker_count
                FROM benchmark_records WHERE job_id=:jid LIMIT 1
            """),
            {"jid": job_id}
        ).fetchone()

        overall_rps = sum(float(self._to_dict(t).get("rps") or 0) for t in table_perf)

        return {
            "job_id":           job_id,
            "total_rows_per_sec": round(overall_rps, 2),
            "benchmark":        self._to_dict(benchmark) if benchmark else None,
            "table_performance": [self._to_dict(t) for t in table_perf],
        }

    def _audit_report(self, db: Session, job_id: str) -> Dict[str, Any]:
        """All operator actions and approvals for the job."""
        actions = db.execute(
            text("""
                SELECT operator_id, action_type, resource_type, resource_id,
                       before_state, after_state, reason, status, created_at
                FROM operations_actions
                WHERE resource_id=:jid OR resource_id IN (
                    SELECT id::text FROM migration_chunks WHERE job_id=:jid
                )
                ORDER BY created_at
            """),
            {"jid": job_id}
        ).fetchall()

        approvals = db.execute(
            text("""
                SELECT approver_id, status, requested_at, reviewed_at, comments
                FROM migration_approvals WHERE job_id=:jid
            """),
            {"jid": job_id}
        ).fetchall()

        return {
            "job_id":          job_id,
            "operator_actions": [self._to_dict(a) for a in actions],
            "approvals":        [self._to_dict(a) for a in approvals],
            "total_actions":    len(actions),
        }

    def _data_quality_report(self, db: Session, job_id: str) -> Dict[str, Any]:
        """Pre-migration data quality scan findings."""
        job = db.execute(
            text("SELECT source_connection_id FROM migration_jobs WHERE id=:id"),
            {"id": job_id}
        ).fetchone()
        connection_id = str(job[0]) if job and job[0] else None

        results = db.execute(
            text("""
                SELECT table_name, check_type, severity, affected_count,
                       affected_pct, details, recommendation, scanned_at
                FROM data_quality_results
                WHERE connection_id=:cid
                ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                         affected_count DESC
            """),
            {"cid": connection_id}
        ).fetchall()

        errors   = sum(1 for r in results if self._to_dict(r).get("severity") == "error")
        warnings = sum(1 for r in results if self._to_dict(r).get("severity") == "warning")

        return {
            "job_id":       job_id,
            "connection_id": connection_id,
            "total_issues": len(results),
            "errors":       errors,
            "warnings":     warnings,
            "can_proceed":  errors == 0,
            "findings":     [self._to_dict(r) for r in results],
        }

    def _compliance_report(self, db: Session, job_id: str) -> Dict[str, Any]:
        """Audit trail for compliance reporting (GDPR/HIPAA/SOC2)."""
        audit_logs = db.execute(
            text("""
                SELECT user_id, user_email, action, resource_type, resource_id,
                       result, ip_address, created_at
                FROM audit_logs
                WHERE resource_id=:jid OR resource_id IN (
                    SELECT id::text FROM migration_chunks WHERE job_id=:jid
                )
                ORDER BY created_at
                LIMIT 1000
            """),
            {"jid": job_id}
        ).fetchall()

        masking_logs = db.execute(
            text("""
                SELECT table_name, column_name, strategy, rows_masked, applied_at
                FROM masking_job_log WHERE job_id=:jid
            """),
            {"jid": job_id}
        ).fetchall()

        return {
            "job_id":           job_id,
            "generated_at":     datetime.datetime.utcnow().isoformat(),
            "audit_events":     len(audit_logs),
            "masked_columns":   len(masking_logs),
            "audit_trail":      [self._to_dict(a) for a in audit_logs[:100]],
            "masking_applied":  [self._to_dict(m) for m in masking_logs],
            "compliance_note":  "This report provides an audit trail for regulatory compliance. "
                                "Retain according to your data retention policy.",
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_report(self, db: Session, report_id: str) -> Optional[Dict]:
        row = db.execute(
            text("SELECT * FROM migration_reports WHERE id=:id"), {"id": report_id}
        ).fetchone()
        return self._to_dict(row) if row else None

    def list_reports(self, db: Session, job_id: str) -> List[Dict]:
        rows = db.execute(
            text("""
                SELECT id, report_type, format, title, generated_by, generated_at
                FROM migration_reports WHERE job_id=:jid ORDER BY generated_at DESC
            """),
            {"jid": job_id}
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def _to_dict(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d

    def _fmt(self, seconds: int) -> str:
        if not seconds or seconds <= 0: return "< 1 minute"
        if seconds < 60:    return f"{seconds}s"
        if seconds < 3600:  m, s = divmod(seconds, 60);   return f"{m}m {s}s"
        if seconds < 86400: h, r = divmod(seconds, 3600); return f"{h}h {r//60}m"
        d, r = divmod(seconds, 86400); return f"{d}d {r//3600}h"
