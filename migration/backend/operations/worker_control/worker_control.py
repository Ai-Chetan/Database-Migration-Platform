"""
Worker Control
File: migration/backend/operations/worker_control/worker_control.py

Live manual control over workers during active migrations.
The Kubernetes Dashboard equivalent for the Migration Platform.

Operations:
    pause_worker      → worker finishes current chunk then stops pulling
    resume_worker     → remove pause signal, worker resumes pulling
    kill_worker       → immediate stop (current chunk is abandoned and retried)
    quarantine_worker → pause + flag as unhealthy (operator investigates)
    scale_workers     → increase or decrease total active worker count for a job
    drain_workers     → gracefully wind down all workers for a job

All actions:
    1. Write to Redis (workers check this on each chunk pull)
    2. Update worker_heartbeats in DB
    3. Log to operations_actions table
    4. Publish event to Event Bus

Redis key conventions:
    migration:worker:{worker_id}:cmd  → "pause" | "kill" | "drain"
    migration:job:{job_id}:worker_count → target worker count override

CHANGES IN THIS VERSION (Stage 1 schema audit fix):
  This file assumed a worker_heartbeats schema that doesn't match the real
  database. Confirmed real columns (via live schema inspection):
      worker_heartbeats: id, worker_name, worker_status, current_chunk_id,
                          hostname, cpu_usage, memory_usage, last_heartbeat,
                          created_at
  It does NOT have worker_id, status, current_job_id, host, pid, or
  error_message — all of which the old version of this file queried
  directly, meaning every method here would have failed at runtime with a
  Postgres "column does not exist" error the first time it actually ran.

  Fixes applied:
    - worker_id      → worker_name (the real identifying column; the
                        "worker_id" concept from Redis keys / the frontend
                        is really this column's value, just renamed)
    - status          → worker_status
    - host            → hostname
    - current_job_id  → does not exist as a column. A worker's current job
                        is now derived by joining through
                        migration_chunks (worker_heartbeats.current_chunk_id
                        -> migration_chunks.id -> migration_chunks.job_id).
    - pid             → does not exist anywhere in the real schema. Dropped
                        from all responses (Stage 2 note: would need a
                        migration to add this column if it's needed later).
    - error_message   → does not exist on worker_heartbeats. Quarantine
                        reasons are now recorded only in operations_actions
                        (which already has a `reason` text column) instead
                        of silently failing to write to a nonexistent
                        column.

  Also fixed: operations_actions.tenant_id and .operator_id are UUID
  columns, but this file's defaults were plain strings ("local",
  "operator") which would fail a Postgres UUID cast. _log_action() now
  accepts real UUIDs (or None) and passes NULL when a valid UUID isn't
  available, instead of a string that would crash the INSERT (previously
  masked by a silent except/rollback in _log_action, so it *looked* like
  ops actions weren't being logged — they were fatally failing on every call).
"""

import datetime
import uuid
import json
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


def _as_uuid_or_none(value):
    """operations_actions.tenant_id / .operator_id are UUID columns.
    Coerce a string to UUID if possible, otherwise return None so the
    INSERT still succeeds (NULL is allowed on both columns)."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


class WorkerControl:

    CMD_TTL = 300   # 5 minutes — commands expire if worker doesn't pick them up

    # ── Individual worker operations ──────────────────────────────────────────

    def pause_worker(
        self,
        db:        Session,
        worker_id: str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Signal a worker to pause after completing its current chunk.
        Worker will stop pulling from queue but won't abandon in-progress work.
        """
        before = self._get_worker_state(db, worker_id)
        redis_client.setex(f"migration:worker:{worker_id}:cmd", self.CMD_TTL, "pause")

        db.execute(
            text("UPDATE worker_heartbeats SET worker_status='PAUSING' WHERE worker_name=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "pause_worker", "worker", worker_id,
                         before, {"worker_status": "PAUSING"}, reason, operator, tenant_id)

        self._publish("worker.paused", worker_id, {"reason": reason})

        logger.info("Worker pause signaled", worker_id=worker_id, reason=reason)
        return {"worker_id": worker_id, "action": "pause", "status": "signaled",
                "message": "Worker will pause after current chunk completes."}

    def resume_worker(
        self,
        db:        Session,
        worker_id: str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """Remove pause/drain signal and allow worker to resume pulling chunks."""
        before = self._get_worker_state(db, worker_id)
        redis_client.delete(f"migration:worker:{worker_id}:cmd")

        db.execute(
            text("UPDATE worker_heartbeats SET worker_status='IDLE' WHERE worker_name=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "resume_worker", "worker", worker_id,
                         before, {"worker_status": "IDLE"}, reason, operator, tenant_id)
        self._publish("worker.resumed", worker_id, {"reason": reason})

        logger.info("Worker resumed", worker_id=worker_id)
        return {"worker_id": worker_id, "action": "resume", "status": "resumed"}

    def kill_worker(
        self,
        db:        Session,
        worker_id: str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Signal a worker to stop immediately. The in-progress chunk will be
        abandoned and automatically retried by another worker (stale chunk recovery).
        Use this only if a worker is stuck or causing issues.
        """
        before = self._get_worker_state(db, worker_id)
        redis_client.setex(f"migration:worker:{worker_id}:cmd", self.CMD_TTL, "kill")

        db.execute(
            text("UPDATE worker_heartbeats SET worker_status='STOPPING' WHERE worker_name=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "kill_worker", "worker", worker_id,
                         before, {"worker_status": "STOPPING"}, reason, operator, tenant_id)
        self._publish("worker.stopped", worker_id, {"reason": reason, "forced": True})

        logger.warning("Worker kill signaled", worker_id=worker_id, reason=reason)
        return {"worker_id": worker_id, "action": "kill", "status": "signaled",
                "message": "Worker will stop immediately. In-progress chunk will be retried."}

    def quarantine_worker(
        self,
        db:        Session,
        worker_id: str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Pause worker AND flag it as quarantined for investigation.
        Quarantined workers are excluded from auto-recovery by stale_chunk_recovery.
        """
        self.pause_worker(db, worker_id, reason, operator, tenant_id)

        redis_client.setex(f"migration:worker:{worker_id}:quarantined",
                           86400, reason or "quarantined by operator")

        db.execute(
            text("UPDATE worker_heartbeats SET worker_status='QUARANTINED' WHERE worker_name=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        # worker_heartbeats has no error_message column, so the reason is
        # recorded in operations_actions (which does have one) instead.
        self._log_action(db, "quarantine_worker", "worker", worker_id,
                         {}, {"worker_status": "QUARANTINED", "reason": reason},
                         reason, operator, tenant_id)

        logger.warning("Worker quarantined", worker_id=worker_id, reason=reason)
        return {"worker_id": worker_id, "action": "quarantine", "status": "quarantined",
                "message": "Worker is quarantined. Review logs before releasing."}

    # ── Job-level worker scaling ───────────────────────────────────────────────

    def scale_workers(
        self,
        db:           Session,
        job_id:       str,
        target_count: int,
        reason:       str = "",
        operator:     str = "operator",
        tenant_id:    str = "local",
    ) -> Dict[str, Any]:
        """
        Set target worker count for a job. Workers read this from Redis
        and the Self-Tuning Engine respects this override.
        """
        if target_count < 0 or target_count > 256:
            return {"error": "target_count must be between 0 and 256"}

        current_count = self._get_active_worker_count(db, job_id)

        redis_client.setex(
            f"migration:job:{job_id}:worker_count",
            3600,   # 1 hour TTL
            str(target_count)
        )

        self._log_action(db, "scale_workers", "job", job_id,
                         {"worker_count": current_count},
                         {"worker_count": target_count},
                         reason, operator, tenant_id)

        self._publish("worker.scaled", job_id, {
            "job_id":        job_id,
            "from_count":    current_count,
            "to_count":      target_count,
            "reason":        reason,
        })

        logger.info("Workers scaled", job_id=job_id,
                    from_count=current_count, to_count=target_count)

        return {
            "job_id":       job_id,
            "action":       "scale_workers",
            "from_count":   current_count,
            "to_count":     target_count,
            "message":      f"Target worker count set to {target_count}. "
                            "New workers must be started manually if scaling up.",
        }

    def drain_all_workers(
        self,
        db:        Session,
        job_id:    str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Gracefully stop all workers for a job.
        Each worker finishes its current chunk then stops.
        """
        workers = self._get_job_workers(db, job_id)
        drained = []

        for worker_id in workers:
            self.pause_worker(db, worker_id, f"drain: {reason}", operator, tenant_id)
            drained.append(worker_id)

        self._log_action(db, "drain_workers", "job", job_id,
                         {"worker_count": len(workers)}, {"drained": len(drained)},
                         reason, operator, tenant_id)

        return {
            "job_id":  job_id,
            "action":  "drain_workers",
            "drained": len(drained),
            "workers": drained,
            "message": "All workers signaled to drain. Job will pause after current chunks complete.",
        }

    def list_workers(self, db: Session, job_id: Optional[str] = None) -> List[Dict]:
        """List all workers with their current status.

        current_job_id is derived by joining through migration_chunks,
        since worker_heartbeats itself has no such column - only
        current_chunk_id, which points at a specific chunk (which in turn
        belongs to a job).
        """
        params: Dict[str, Any] = {}
        where  = ""
        if job_id:
            where = "WHERE mc.job_id = :jid"
            params["jid"] = job_id

        rows = db.execute(
            text(f"""
                SELECT
                    wh.worker_name,
                    wh.worker_status,
                    mc.job_id            AS current_job_id,
                    wh.current_chunk_id,
                    wh.last_heartbeat,
                    wh.hostname,
                    wh.cpu_usage,
                    wh.memory_usage
                FROM worker_heartbeats wh
                LEFT JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                {where}
                ORDER BY wh.last_heartbeat DESC
            """),
            params
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):        d[k] = str(v)
                if hasattr(v, "isoformat"):  d[k] = v.isoformat()
            # Frontend-facing aliases: the frontend's Worker type predates this
            # schema audit and expects worker_id/status/host. Real DB columns
            # are worker_name/worker_status/hostname - keep both so neither
            # side has to change right now. There is no `pid` column anywhere
            # in the real schema, so it's intentionally omitted (frontend
            # treats it as optional).
            d["worker_id"] = d["worker_name"]
            d["status"]    = d["worker_status"]
            d["host"]      = d.get("hostname")
            # Add Redis command if any
            cmd_key = f"migration:worker:{d['worker_name']}:cmd"
            pending_cmd = redis_client.get(cmd_key)
            d["pending_command"] = pending_cmd.decode() if pending_cmd else None
            d["is_quarantined"]  = bool(
                redis_client.exists(f"migration:worker:{d['worker_name']}:quarantined")
            )
            result.append(d)
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_worker_state(self, db: Session, worker_id: str) -> Dict:
        row = db.execute(
            text("""
                SELECT wh.worker_status, mc.job_id AS current_job_id, wh.current_chunk_id
                FROM worker_heartbeats wh
                LEFT JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE wh.worker_name = :wid
            """),
            {"wid": worker_id}
        ).fetchone()
        return dict(row._mapping) if row else {}

    def _get_active_worker_count(self, db: Session, job_id: str) -> int:
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id = :jid AND wh.worker_status IN ('BUSY','IDLE')
                AND wh.last_heartbeat > NOW() - INTERVAL '2 minutes'
            """),
            {"jid": job_id}
        ).fetchone()
        return row[0] if row else 0

    def _get_job_workers(self, db: Session, job_id: str) -> List[str]:
        rows = db.execute(
            text("""
                SELECT wh.worker_name FROM worker_heartbeats wh
                JOIN migration_chunks mc ON mc.id = wh.current_chunk_id
                WHERE mc.job_id = :jid AND wh.worker_status IN ('BUSY','IDLE')
                AND wh.last_heartbeat > NOW() - INTERVAL '2 minutes'
            """),
            {"jid": job_id}
        ).fetchall()
        return [r[0] for r in rows]

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
                    "rid":    resource_id,
                    "before": json.dumps(before, default=str),
                    "after":  json.dumps(after, default=str),
                    "reason": reason, "now": datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to log operations action", error=str(e))
            db.rollback()

    def _publish(self, event_type: str, resource_id: str, payload: Dict):
        try:
            from backend.kernel.event_bus.event_bus import EventBus
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()
            try:
                EventBus.publish(
                    event_type=event_type,
                    source_service="operations_console",
                    resource_type="worker",
                    resource_id=resource_id,
                    payload=payload,
                    correlation_id=payload.get("job_id", resource_id),
                    db=db,
                )
            finally:
                db.close()
        except Exception:
            pass
