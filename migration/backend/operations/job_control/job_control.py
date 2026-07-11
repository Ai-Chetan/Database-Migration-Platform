"""
Job Control + Maintenance Mode
File: migration/backend/operations/job_control/job_control.py

Job-level operational controls and platform maintenance mode.

Job Controls:
    pause_job          → pause all workers for a job gracefully
    resume_job         → resume a paused job
    cancel_job         → permanently cancel (marks failed, workers drain)
    rerun_validation   → re-run post-migration validation for a job
    get_live_stats     → real-time job stats (progress, throughput, ETA)

Maintenance Mode:
    enable_maintenance  → block new job starts, drain existing workers
    disable_maintenance → return platform to normal operation
    emergency_stop      → immediate halt of ALL jobs and workers

Maintenance mode is useful when:
    - Database maintenance is required
    - Platform upgrade is being deployed
    - An infrastructure issue is detected
    - An operator needs to investigate a problem
"""

import datetime
import json
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


class JobControl:

    # ── Job operations ─────────────────────────────────────────────────────────

    def pause_job(
        self,
        db:        Session,
        job_id:    str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Pause a running job. All workers stop after current chunk.
        Job status → paused. Can be resumed later.
        """
        row = db.execute(
            text("SELECT status FROM migration_jobs WHERE id=:id"),
            {"id": job_id}
        ).fetchone()

        if not row:
            return {"error": f"Job {job_id} not found"}

        before_status = row[0]
        if before_status not in ("running", "planning"):
            return {"error": f"Cannot pause job in status '{before_status}'"}

        # Signal all workers for this job
        workers = db.execute(
            text("""
                SELECT worker_id FROM worker_heartbeats
                WHERE current_job_id=:jid AND status IN ('BUSY','IDLE')
            """),
            {"jid": job_id}
        ).fetchall()

        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "pause")

        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='paused',
                    error_message=:msg,
                    updated_at=:now
                WHERE id=:id
            """),
            {"msg": f"Paused by operator: {reason}",
             "now": datetime.datetime.utcnow(), "id": job_id}
        )
        db.commit()

        self._log_action(db, "pause_job", "job", job_id,
                         {"status": before_status}, {"status": "paused"},
                         reason, operator, tenant_id)
        self._publish("job.paused", job_id, {"reason": reason, "paused_by": operator})

        logger.info("Job paused by operator", job_id=job_id, reason=reason)
        return {
            "job_id":          job_id,
            "action":          "pause",
            "workers_signaled": len(workers),
            "message":         f"Job paused. {len(workers)} worker(s) will stop after current chunk.",
        }

    def resume_job(
        self,
        db:        Session,
        job_id:    str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """Resume a paused job. Clears pause signals from all its workers."""
        row = db.execute(
            text("SELECT status FROM migration_jobs WHERE id=:id"),
            {"id": job_id}
        ).fetchone()

        if not row:
            return {"error": f"Job {job_id} not found"}

        before_status = row[0]
        if before_status != "paused":
            return {"error": f"Job is not paused (current status: '{before_status}')"}

        # Clear worker pause commands
        workers = db.execute(
            text("SELECT worker_id FROM worker_heartbeats WHERE current_job_id=:jid"),
            {"jid": job_id}
        ).fetchall()
        for w in workers:
            redis_client.delete(f"migration:worker:{w[0]}:cmd")

        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='running', error_message=NULL, updated_at=:now
                WHERE id=:id
            """),
            {"now": datetime.datetime.utcnow(), "id": job_id}
        )
        db.commit()

        self._log_action(db, "resume_job", "job", job_id,
                         {"status": "paused"}, {"status": "running"},
                         reason, operator, tenant_id)
        self._publish("job.resumed", job_id, {"reason": reason})

        logger.info("Job resumed by operator", job_id=job_id)
        return {
            "job_id":  job_id,
            "action":  "resume",
            "message": "Job resumed. Workers will begin pulling chunks.",
        }

    def cancel_job(
        self,
        db:        Session,
        job_id:    str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Permanently cancel a job. Cannot be undone.
        Workers drain (finish current chunk), job marked as cancelled.
        """
        if not reason:
            return {"error": "reason is required when cancelling a job"}

        row = db.execute(
            text("SELECT status FROM migration_jobs WHERE id=:id"),
            {"id": job_id}
        ).fetchone()

        if not row:
            return {"error": f"Job {job_id} not found"}

        before_status = row[0]
        if before_status in ("completed", "cancelled"):
            return {"error": f"Job is already {before_status}"}

        # Signal workers to drain
        workers = db.execute(
            text("SELECT worker_id FROM worker_heartbeats WHERE current_job_id=:jid"),
            {"jid": job_id}
        ).fetchall()
        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "drain")

        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='cancelled',
                    error_message=:msg,
                    completed_at=:now,
                    updated_at=:now
                WHERE id=:id
            """),
            {"msg": f"Cancelled by operator: {reason}",
             "now": datetime.datetime.utcnow(), "id": job_id}
        )
        db.commit()

        self._log_action(db, "cancel_job", "job", job_id,
                         {"status": before_status}, {"status": "cancelled"},
                         reason, operator, tenant_id)
        self._publish("job.cancelled", job_id, {"reason": reason, "cancelled_by": operator})

        logger.warning("Job cancelled by operator", job_id=job_id, reason=reason)
        return {
            "job_id":  job_id,
            "action":  "cancel",
            "message": f"Job cancelled. {len(workers)} worker(s) will drain gracefully.",
            "warning": "This action cannot be undone. Use rollback if target data needs cleanup.",
        }

    def rerun_validation(
        self,
        db:        Session,
        job_id:    str,
        table_name: Optional[str] = None,
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Re-run post-migration validation for a completed job.
        Optionally target a specific table.
        """
        # Find completed chunks for this job (optionally filtered by table)
        conditions = ["mc.job_id=:jid", "mc.status='completed'"]
        params: Dict[str, Any] = {"jid": job_id}

        if table_name:
            conditions.append("mt.table_name=:tname")
            params["tname"] = table_name

        chunks = db.execute(
            text(f"""
                SELECT mc.id, mc.pk_start, mc.pk_end, mt.table_name
                FROM migration_chunks mc
                JOIN migration_tables mt ON mc.table_id = mt.id
                WHERE {' AND '.join(conditions)}
                LIMIT 1000
            """),
            params
        ).fetchall()

        if not chunks:
            return {"error": "No completed chunks found to re-validate"}

        # Reset validation status on these chunks to trigger re-verification
        chunk_ids = [str(c[0]) for c in chunks]
        db.execute(
            text(f"""
                UPDATE migration_chunks
                SET validation_status='pending'
                WHERE id = ANY(ARRAY[{','.join([f"'{cid}'" for cid in chunk_ids])}]::uuid[])
            """)
        )
        db.commit()

        self._log_action(db, "rerun_validation", "job", job_id,
                         {"chunks_affected": len(chunk_ids)},
                         {"validation_status": "pending", "table": table_name or "all"},
                         f"Manual re-validation by {operator}", operator, tenant_id)

        return {
            "job_id":         job_id,
            "action":         "rerun_validation",
            "chunks_affected": len(chunk_ids),
            "table":          table_name or "all tables",
            "message":        f"Validation reset for {len(chunk_ids)} chunk(s). "
                              "Workers will re-verify on next cycle.",
        }

    def get_live_stats(self, db: Session, job_id: str) -> Dict[str, Any]:
        """
        Real-time job statistics for the Operations Console dashboard.
        Computes progress, throughput, ETA, error rate in one query.
        """
        row = db.execute(
            text("""
                SELECT
                    mj.status,
                    mj.started_at,
                    COUNT(mc.id)                                          AS total_chunks,
                    COUNT(*) FILTER (WHERE mc.status='completed')         AS completed_chunks,
                    COUNT(*) FILTER (WHERE mc.status='failed')            AS failed_chunks,
                    COUNT(*) FILTER (WHERE mc.status='running')           AS running_chunks,
                    COUNT(*) FILTER (WHERE mc.status='pending')           AS pending_chunks,
                    COUNT(*) FILTER (WHERE mc.status='skipped')           AS skipped_chunks,
                    SUM(mc.rows_processed)                                AS rows_migrated,
                    AVG(mc.duration_ms) FILTER (WHERE mc.duration_ms > 0) AS avg_chunk_ms,
                    MAX(mc.completed_at)                                  AS last_completed_at,
                    COUNT(DISTINCT wh.worker_id) FILTER (
                        WHERE wh.last_heartbeat > NOW() - INTERVAL '2 minutes'
                    )                                                     AS active_workers
                FROM migration_jobs mj
                LEFT JOIN migration_chunks mc ON mc.job_id = mj.id
                LEFT JOIN worker_heartbeats wh ON wh.current_job_id = mj.id
                WHERE mj.id = :jid
                GROUP BY mj.id, mj.status, mj.started_at
            """),
            {"jid": job_id}
        ).fetchone()

        if not row:
            return {}

        d = dict(row._mapping)

        # Compute progress %
        total     = int(d.get("total_chunks") or 0)
        completed = int(d.get("completed_chunks") or 0)
        failed    = int(d.get("failed_chunks") or 0)
        progress  = round(completed / total * 100, 1) if total > 0 else 0

        # Compute throughput (rows/sec over last 5 min)
        tput_row = db.execute(
            text("""
                SELECT SUM(rows_processed)::float / 300 AS rps
                FROM migration_chunks
                WHERE job_id=:jid AND completed_at >= NOW() - INTERVAL '5 minutes'
            """),
            {"jid": job_id}
        ).fetchone()
        rps = round(float(tput_row[0] or 0), 1) if tput_row else 0

        # ETA
        pending    = int(d.get("pending_chunks") or 0)
        avg_ms     = float(d.get("avg_chunk_ms") or 0)
        active_w   = int(d.get("active_workers") or 1)
        eta_sec    = int((pending * avg_ms / 1000) / max(active_w, 1)) if avg_ms > 0 else None

        # Format timestamps
        for k, v in d.items():
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
            if hasattr(v, "hex"):       d[k] = str(v)

        return {
            "job_id":          job_id,
            "status":          d.get("status"),
            "progress_pct":    progress,
            "total_chunks":    total,
            "completed_chunks": completed,
            "failed_chunks":   failed,
            "running_chunks":  int(d.get("running_chunks") or 0),
            "pending_chunks":  pending,
            "skipped_chunks":  int(d.get("skipped_chunks") or 0),
            "rows_migrated":   int(d.get("rows_migrated") or 0),
            "rows_per_sec":    rps,
            "active_workers":  active_w,
            "avg_chunk_ms":    round(avg_ms, 0),
            "eta_seconds":     eta_sec,
            "eta_str":         self._fmt(eta_sec) if eta_sec else "unknown",
            "error_rate_pct":  round(failed / max(total, 1) * 100, 2),
        }

    # ── Maintenance Mode ───────────────────────────────────────────────────────

    def enable_maintenance(
        self,
        db:        Session,
        reason:    str,
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Enable maintenance mode for the tenant.
        New jobs will not start. Existing workers finish current chunks then stop.
        """
        if not reason:
            return {"error": "reason is required for maintenance mode"}

        redis_client.set("migration:maintenance:active", "1")

        db.execute(
            text("""
                INSERT INTO maintenance_mode (tenant_id, is_active, reason, activated_by, activated_at, updated_at)
                VALUES (:tid, TRUE, :reason, :op, :now, :now)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    is_active=TRUE, reason=:reason, activated_by=:op,
                    activated_at=:now, updated_at=:now
            """),
            {"tid": tenant_id, "reason": reason, "op": operator,
             "now": datetime.datetime.utcnow()}
        )
        db.commit()

        self._log_action(db, "maintenance_mode_on", "system", tenant_id,
                         {"maintenance": False}, {"maintenance": True, "reason": reason},
                         reason, operator, tenant_id)
        self._publish("system.maintenance_on", tenant_id, {"reason": reason})

        logger.warning("Maintenance mode ENABLED", tenant_id=tenant_id, reason=reason)
        return {
            "maintenance_mode": True,
            "reason":           reason,
            "message":          "Maintenance mode enabled. New jobs blocked. "
                                "Workers will drain after current chunks.",
        }

    def disable_maintenance(
        self,
        db:        Session,
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """Disable maintenance mode and return platform to normal operation."""
        redis_client.delete("migration:maintenance:active")

        db.execute(
            text("""
                UPDATE maintenance_mode SET
                    is_active=FALSE, deactivated_at=:now, updated_at=:now
                WHERE tenant_id=:tid
            """),
            {"now": datetime.datetime.utcnow(), "tid": tenant_id}
        )
        db.commit()

        self._log_action(db, "maintenance_mode_off", "system", tenant_id,
                         {"maintenance": True}, {"maintenance": False},
                         "Maintenance mode disabled", operator, tenant_id)
        self._publish("system.maintenance_off", tenant_id, {})

        logger.info("Maintenance mode DISABLED", tenant_id=tenant_id)
        return {"maintenance_mode": False, "message": "Maintenance mode disabled. Platform resumed."}

    def emergency_stop(
        self,
        db:        Session,
        reason:    str,
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        EMERGENCY: Immediately halt ALL running jobs and signal ALL workers to stop.
        Use only in critical situations. Jobs will need manual restart.
        """
        if not reason:
            return {"error": "reason is required for emergency stop"}

        # Enable maintenance mode first
        self.enable_maintenance(db, f"EMERGENCY STOP: {reason}", operator, tenant_id)

        # Kill all active workers immediately
        workers = db.execute(
            text("""
                SELECT worker_id FROM worker_heartbeats
                WHERE status IN ('BUSY','IDLE')
                AND last_heartbeat > NOW() - INTERVAL '5 minutes'
            """)
        ).fetchall()

        killed = 0
        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "kill")
            killed += 1

        # Pause all running jobs
        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='paused',
                    error_message=:msg,
                    updated_at=:now
                WHERE status='running'
            """),
            {"msg": f"EMERGENCY STOP: {reason}", "now": datetime.datetime.utcnow()}
        )
        db.commit()

        self._log_action(db, "emergency_stop", "system", tenant_id,
                         {"workers_active": killed},
                         {"workers_killed": killed, "reason": reason},
                         reason, operator, tenant_id)
        self._publish("system.emergency_stop", tenant_id,
                      {"reason": reason, "workers_killed": killed})

        logger.critical("EMERGENCY STOP executed",
                        tenant_id=tenant_id, reason=reason, workers_killed=killed)
        return {
            "action":         "emergency_stop",
            "workers_killed": killed,
            "maintenance":    True,
            "reason":         reason,
            "warning":        "All jobs paused. Workers signaled to stop immediately. "
                              "Review logs before resuming.",
        }

    def get_maintenance_status(self, db: Session, tenant_id: str = "local") -> Dict[str, Any]:
        row = db.execute(
            text("SELECT * FROM maintenance_mode WHERE tenant_id=:tid"),
            {"tid": tenant_id}
        ).fetchone()
        if not row:
            return {"maintenance_mode": False, "tenant_id": tenant_id}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d

    # ── Private ───────────────────────────────────────────────────────────────

    def _log_action(self, db, action_type, resource_type, resource_id,
                    before, after, reason, operator, tenant_id):
        try:
            db.execute(
                text("""
                    INSERT INTO operations_actions
                        (id, tenant_id, operator_id, action_type, resource_type,
                         resource_id, before_state, after_state, reason, created_at)
                    VALUES
                        (gen_random_uuid(), :tid, :op, :atype, :rtype,
                         :rid, :before::jsonb, :after::jsonb, :reason, :now)
                """),
                {
                    "tid":    tenant_id, "op":     operator,
                    "atype":  action_type, "rtype": resource_type,
                    "rid":    str(resource_id),
                    "before": json.dumps(before, default=str),
                    "after":  json.dumps(after, default=str),
                    "reason": reason, "now": datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to log job action", error=str(e))
            try: db.rollback()
            except Exception: pass

    def _publish(self, event_type, resource_id, payload):
        try:
            from backend.kernel.event_bus.event_bus import EventBus
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()
            try:
                EventBus.publish(
                    event_type=event_type,
                    source_service="operations_console",
                    resource_type="job",
                    resource_id=str(resource_id),
                    payload=payload,
                    correlation_id=str(resource_id),
                    db=db,
                )
            finally:
                db.close()
        except Exception:
            pass

    def _fmt(self, seconds: int) -> str:
        if not seconds or seconds <= 0: return "< 1 minute"
        if seconds < 60:    return f"{seconds}s"
        if seconds < 3600:  m, s = divmod(seconds, 60);   return f"{m}m {s}s"
        if seconds < 86400: h, r = divmod(seconds, 3600); return f"{h}h {r//60}m"
        d, r = divmod(seconds, 86400); return f"{d}d {r//3600}h"
