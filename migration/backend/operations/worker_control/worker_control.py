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
"""

import datetime
import uuid
import json
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


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
            text("UPDATE worker_heartbeats SET status='PAUSING' WHERE worker_id=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "pause_worker", "worker", worker_id,
                         before, {"status": "PAUSING"}, reason, operator, tenant_id)

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
            text("UPDATE worker_heartbeats SET status='IDLE' WHERE worker_id=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "resume_worker", "worker", worker_id,
                         before, {"status": "IDLE"}, reason, operator, tenant_id)
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
            text("UPDATE worker_heartbeats SET status='STOPPING' WHERE worker_id=:wid"),
            {"wid": worker_id}
        )
        db.commit()

        self._log_action(db, "kill_worker", "worker", worker_id,
                         before, {"status": "STOPPING"}, reason, operator, tenant_id)
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
            text("""
                UPDATE worker_heartbeats
                SET status='QUARANTINED',
                    error_message=:msg
                WHERE worker_id=:wid
            """),
            {"msg": f"QUARANTINED: {reason}", "wid": worker_id}
        )
        db.commit()

        self._log_action(db, "quarantine_worker", "worker", worker_id,
                         {}, {"status": "QUARANTINED", "reason": reason},
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
        """List all workers with their current status."""
        params: Dict[str, Any] = {}
        where  = ""
        if job_id:
            where = "WHERE current_job_id=:jid"
            params["jid"] = job_id

        rows = db.execute(
            text(f"""
                SELECT worker_id, status, current_job_id, current_chunk_id,
                       last_heartbeat, host, pid, error_message
                FROM worker_heartbeats
                {where}
                ORDER BY last_heartbeat DESC
            """),
            params
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):        d[k] = str(v)
                if hasattr(v, "isoformat"):  d[k] = v.isoformat()
            # Add Redis command if any
            cmd_key = f"migration:worker:{d['worker_id']}:cmd"
            pending_cmd = redis_client.get(cmd_key)
            d["pending_command"] = pending_cmd.decode() if pending_cmd else None
            d["is_quarantined"]  = bool(
                redis_client.exists(f"migration:worker:{d['worker_id']}:quarantined")
            )
            result.append(d)
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_worker_state(self, db: Session, worker_id: str) -> Dict:
        row = db.execute(
            text("SELECT status, current_job_id, current_chunk_id FROM worker_heartbeats WHERE worker_id=:wid"),
            {"wid": worker_id}
        ).fetchone()
        return dict(row._mapping) if row else {}

    def _get_active_worker_count(self, db: Session, job_id: str) -> int:
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM worker_heartbeats
                WHERE current_job_id=:jid AND status IN ('BUSY','IDLE')
                AND last_heartbeat > NOW() - INTERVAL '2 minutes'
            """),
            {"jid": job_id}
        ).fetchone()
        return row[0] if row else 0

    def _get_job_workers(self, db: Session, job_id: str) -> List[str]:
        rows = db.execute(
            text("""
                SELECT worker_id FROM worker_heartbeats
                WHERE current_job_id=:jid AND status IN ('BUSY','IDLE')
                AND last_heartbeat > NOW() - INTERVAL '2 minutes'
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
                         :rid, :before::jsonb, :after::jsonb, :reason, :now)
                """),
                {
                    "tid":    tenant_id, "op":     operator,
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
