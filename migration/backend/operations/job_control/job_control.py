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

CHANGES IN THIS VERSION (Stage 1 schema audit fix):
  Four real bugs found and fixed, confirmed against the live schema:

  1. worker_heartbeats: same bug as worker_control.py — worker_id/status/
     current_job_id don't exist (real columns: worker_name, worker_status;
     current_job_id must be derived by joining migration_chunks). Every
     query here that touched worker_heartbeats would have failed outright.

  2. migration_jobs has NO `updated_at` and NO `error_message` column.
     The old code wrote pause/cancel reasons into a nonexistent
     error_message column and stamped a nonexistent updated_at — both
     would raise "column does not exist" at the database level.
     migration_jobs DOES already have purpose-built columns for exactly
     this: paused_at, paused_by (uuid), cancelled_at, cancelled_by (uuid),
     cancellation_reason (text), and last_error (text, for genuine
     execution errors — not administrative pause/cancel notes). This
     version uses those real columns instead.

  3. Same UUID-cast issue as worker_control.py: operations_actions.tenant_id
     / .operator_id and migration_jobs.paused_by / .cancelled_by are all
     UUID columns, but this file's operator/tenant_id defaults were plain
     strings ("operator", "local") that would fail a Postgres UUID cast.
     Now coerced via _as_uuid_or_none(), which passes NULL when a real
     UUID isn't available rather than crashing the query.

  4. maintenance_mode has NO plain unique constraint on tenant_id (only a
     partial unique index that applies solely when tenant_id IS NULL, for
     enforcing a single global maintenance row). The old
     `ON CONFLICT (tenant_id) DO UPDATE` clause doesn't match any real
     constraint and would fail at the SQL level for every call. Replaced
     with an explicit check-then-update-or-insert that handles both the
     global (tenant_id IS NULL) and per-tenant cases correctly.
"""

import datetime
import json
import uuid
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


def _as_uuid_or_none(value):
    """Coerce a string to UUID if possible, else None (for nullable UUID columns)."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


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

        # Signal all workers for this job (derived via migration_chunks,
        # since worker_heartbeats has no current_job_id column)
        workers = db.execute(
            text("""
                SELECT DISTINCT wh.worker_name
                FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id=:jid AND wh.worker_status IN ('BUSY','IDLE')
            """),
            {"jid": job_id}
        ).fetchall()

        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "pause")

        op_uuid = _as_uuid_or_none(operator)
        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='paused',
                    paused_at=:now,
                    paused_by=:op
                WHERE id=:id
            """),
            {"now": datetime.datetime.utcnow(), "op": op_uuid, "id": job_id}
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
            text("""
                SELECT DISTINCT wh.worker_name
                FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id=:jid
            """),
            {"jid": job_id}
        ).fetchall()
        for w in workers:
            redis_client.delete(f"migration:worker:{w[0]}:cmd")

        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='running', paused_at=NULL, paused_by=NULL
                WHERE id=:id
            """),
            {"id": job_id}
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
            text("""
                SELECT DISTINCT wh.worker_name
                FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id=:jid
            """),
            {"jid": job_id}
        ).fetchall()
        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "drain")

        op_uuid = _as_uuid_or_none(operator)
        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='cancelled',
                    cancelled_at=:now,
                    cancelled_by=:op,
                    cancellation_reason=:reason,
                    completed_at=:now
                WHERE id=:id
            """),
            {"reason": reason, "now": datetime.datetime.utcnow(),
             "op": op_uuid, "id": job_id}
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

        chunk_ids = [str(c[0]) for c in chunks]
        db.execute(
            text("""
                UPDATE migration_chunks
                SET validation_status='pending'
                WHERE id = ANY(CAST(:ids AS uuid[]))
            """),
            {"ids": chunk_ids}
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
                    MAX(mc.completed_at)                                  AS last_completed_at
                FROM migration_jobs mj
                LEFT JOIN migration_chunks mc ON mc.job_id = mj.id
                WHERE mj.id = :jid
                GROUP BY mj.id, mj.status, mj.started_at
            """),
            {"jid": job_id}
        ).fetchone()

        if not row:
            return {}

        d = dict(row._mapping)

        # Active workers derived separately via the migration_chunks join
        # (worker_heartbeats has no current_job_id column to join on directly)
        active_row = db.execute(
            text("""
                SELECT COUNT(DISTINCT wh.worker_name)
                FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id = :jid
                AND wh.last_heartbeat > NOW() - INTERVAL '2 minutes'
            """),
            {"jid": job_id}
        ).fetchone()
        active_workers = int(active_row[0] or 0) if active_row else 0

        total     = int(d.get("total_chunks") or 0)
        completed = int(d.get("completed_chunks") or 0)
        failed    = int(d.get("failed_chunks") or 0)
        progress  = round(completed / total * 100, 1) if total > 0 else 0

        tput_row = db.execute(
            text("""
                SELECT SUM(rows_processed)::float / 300 AS rps
                FROM migration_chunks
                WHERE job_id=:jid AND completed_at >= NOW() - INTERVAL '5 minutes'
            """),
            {"jid": job_id}
        ).fetchone()
        rps = round(float(tput_row[0] or 0), 1) if tput_row else 0

        pending    = int(d.get("pending_chunks") or 0)
        avg_ms     = float(d.get("avg_chunk_ms") or 0)
        active_w   = max(active_workers, 1)
        eta_sec    = int((pending * avg_ms / 1000) / active_w) if avg_ms > 0 else None

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
            "active_workers":  active_workers,
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

        tid = _as_uuid_or_none(tenant_id)
        op_uuid = _as_uuid_or_none(operator)
        now = datetime.datetime.utcnow()

        # No plain unique constraint exists on tenant_id (only a partial
        # index for the NULL/global case), so ON CONFLICT can't target it.
        # Do an explicit update-or-insert instead.
        where_clause = "tenant_id IS NULL" if tid is None else "tenant_id = :tid"
        updated = db.execute(
            text(f"""
                UPDATE maintenance_mode SET
                    is_active=TRUE, reason=:reason, activated_by=:op,
                    activated_at=:now, updated_at=:now
                WHERE {where_clause}
            """),
            {"reason": reason, "op": op_uuid, "now": now, "tid": tid}
        )
        db.commit()

        if updated.rowcount == 0:
            db.execute(
                text("""
                    INSERT INTO maintenance_mode
                        (tenant_id, is_active, reason, activated_by, activated_at, updated_at)
                    VALUES (:tid, TRUE, :reason, :op, :now, :now)
                """),
                {"tid": tid, "reason": reason, "op": op_uuid, "now": now}
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

        tid = _as_uuid_or_none(tenant_id)
        where_clause = "tenant_id IS NULL" if tid is None else "tenant_id = :tid"
        db.execute(
            text(f"""
                UPDATE maintenance_mode SET
                    is_active=FALSE, deactivated_at=:now, updated_at=:now
                WHERE {where_clause}
            """),
            {"now": datetime.datetime.utcnow(), "tid": tid}
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

        self.enable_maintenance(db, f"EMERGENCY STOP: {reason}", operator, tenant_id)

        workers = db.execute(
            text("""
                SELECT worker_name FROM worker_heartbeats
                WHERE worker_status IN ('BUSY','IDLE')
                AND last_heartbeat > NOW() - INTERVAL '5 minutes'
            """)
        ).fetchall()

        killed = 0
        for w in workers:
            redis_client.setex(f"migration:worker:{w[0]}:cmd", 300, "kill")
            killed += 1

        op_uuid = _as_uuid_or_none(operator)
        db.execute(
            text("""
                UPDATE migration_jobs SET
                    status='paused',
                    paused_at=:now,
                    paused_by=:op
                WHERE status='running'
            """),
            {"now": datetime.datetime.utcnow(), "op": op_uuid}
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
        tid = _as_uuid_or_none(tenant_id)
        where_clause = "tenant_id IS NULL" if tid is None else "tenant_id = :tid"
        row = db.execute(
            text(f"SELECT * FROM maintenance_mode WHERE {where_clause} LIMIT 1"),
            {"tid": tid}
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
                         :rid, CAST(:before AS jsonb), CAST(:after AS jsonb), :reason, :now)
                """),
                {
                    "tid":    _as_uuid_or_none(tenant_id),
                    "op":     _as_uuid_or_none(operator),
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
