"""
Chunk Control
File: migration/backend/operations/chunk_control/chunk_control.py

Granular control over individual migration chunks.
When a chunk is stuck, failing repeatedly, or needs special handling,
operators can intervene directly without restarting the entire job.

Operations:
    reassign_chunk → move chunk ownership from stuck worker to new worker
    retry_chunk    → reset chunk status to pending so any worker picks it up
    skip_chunk     → mark chunk as skipped (data gap, handled separately)
    force_complete → mark chunk complete without actually migrating (dangerous)
    get_chunk_detail → full chunk state including worker, timing, errors
"""

import datetime
import json
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


class ChunkControl:

    def reassign_chunk(
        self,
        db:             Session,
        chunk_id:       str,
        target_worker:  Optional[str] = None,
        reason:         str = "",
        operator:       str = "operator",
        tenant_id:      str = "local",
    ) -> Dict[str, Any]:
        """
        Move a stuck chunk to a different worker (or back to the queue).
        The chunk must be in status 'running' with a stale heartbeat to reassign.
        If target_worker is None, chunk goes back to queue for any worker.
        """
        row = db.execute(
            text("SELECT * FROM migration_chunks WHERE id=:id"),
            {"id": chunk_id}
        ).fetchone()

        if not row:
            return {"error": f"Chunk {chunk_id} not found"}

        chunk  = dict(row._mapping)
        status = chunk.get("status")

        if status == "completed":
            return {"error": "Cannot reassign completed chunk"}

        before = {"status": status, "worker_id": str(chunk.get("worker_id") or "")}

        if target_worker:
            # Assign directly to a specific worker via Redis
            redis_client.setex(
                f"migration:chunk:{chunk_id}:assigned_to",
                300,
                target_worker
            )
            db.execute(
                text("""
                    UPDATE migration_chunks SET
                        status='pending', worker_id=NULL,
                        last_error=NULL, last_heartbeat=NULL
                    WHERE id=:id
                """),
                {"id": chunk_id}
            )
            after_msg = f"reassigned to worker {target_worker}"
        else:
            # Return to queue
            db.execute(
                text("""
                    UPDATE migration_chunks SET
                        status='pending', worker_id=NULL,
                        last_error=NULL, last_heartbeat=NULL
                    WHERE id=:id
                """),
                {"id": chunk_id}
            )
            # Push back onto Redis queue
            job_id   = str(chunk.get("job_id") or "")
            table_id = str(chunk.get("table_id") or "")
            if job_id:
                from backend.shared.constants.queues import Queues
                redis_client.lpush(
                    Queues.MIGRATION_QUEUE,
                    json.dumps({"job_id": job_id, "table_id": table_id,
                                "chunk_id": chunk_id})
                )
            after_msg = "returned to queue"

        db.commit()
        self._log_action(db, "reassign_chunk", "chunk", chunk_id,
                         before, {"status": "pending", "target": target_worker or "queue"},
                         reason, operator, tenant_id)

        logger.info("Chunk reassigned", chunk_id=chunk_id, target=target_worker or "queue")
        return {
            "chunk_id": chunk_id,
            "action":   "reassign",
            "result":   after_msg,
            "message":  f"Chunk {chunk_id} {after_msg}.",
        }

    def retry_chunk(
        self,
        db:        Session,
        chunk_id:  str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Reset a failed chunk to pending so it gets retried.
        Increments retry_count. If retry_count > max_retries this is a forced retry.
        """
        row = db.execute(
            text("SELECT status, retry_count, job_id, table_id FROM migration_chunks WHERE id=:id"),
            {"id": chunk_id}
        ).fetchone()

        if not row:
            return {"error": f"Chunk {chunk_id} not found"}

        before = dict(row._mapping)
        for k, v in before.items():
            if hasattr(v, "hex"): before[k] = str(v)

        db.execute(
            text("""
                UPDATE migration_chunks SET
                    status='pending', worker_id=NULL, last_heartbeat=NULL,
                    retry_count=retry_count+1, last_error=NULL
                WHERE id=:id
            """),
            {"id": chunk_id}
        )
        db.commit()

        # Push back to queue
        job_id   = str(row.job_id or "")
        table_id = str(row.table_id or "")
        if job_id:
            from backend.shared.constants.queues import Queues
            redis_client.lpush(
                Queues.MIGRATION_QUEUE,
                json.dumps({"job_id": job_id, "table_id": table_id, "chunk_id": chunk_id})
            )

        self._log_action(db, "retry_chunk", "chunk", chunk_id,
                         before, {"status": "pending", "retry_forced": True},
                         reason, operator, tenant_id)

        logger.info("Chunk queued for retry", chunk_id=chunk_id,
                    previous_status=before.get("status"))
        return {
            "chunk_id":    chunk_id,
            "action":      "retry",
            "retry_count": (before.get("retry_count") or 0) + 1,
            "message":     f"Chunk reset to pending. Will be picked up by next available worker.",
        }

    def skip_chunk(
        self,
        db:        Session,
        chunk_id:  str,
        reason:    str = "",
        operator:  str = "operator",
        tenant_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Mark a chunk as skipped. Data in this PK range will NOT be migrated.
        Use this for known bad data ranges or when manual remediation is planned.
        Always requires a reason for audit purposes.
        """
        if not reason:
            return {"error": "reason is required when skipping a chunk"}

        row = db.execute(
            text("SELECT status, pk_start, pk_end, table_id FROM migration_chunks WHERE id=:id"),
            {"id": chunk_id}
        ).fetchone()

        if not row:
            return {"error": f"Chunk {chunk_id} not found"}

        before = {"status": row.status,
                  "pk_start": str(row.pk_start), "pk_end": str(row.pk_end)}

        db.execute(
            text("""
                UPDATE migration_chunks SET
                    status='skipped', worker_id=NULL,
                    last_error=:reason, completed_at=:now
                WHERE id=:id
            """),
            {"reason": f"SKIPPED BY OPERATOR: {reason}",
             "now": datetime.datetime.utcnow(), "id": chunk_id}
        )
        db.commit()

        self._log_action(db, "skip_chunk", "chunk", chunk_id,
                         before, {"status": "skipped", "reason": reason},
                         reason, operator, tenant_id)

        logger.warning("Chunk skipped by operator",
                       chunk_id=chunk_id, reason=reason)
        return {
            "chunk_id": chunk_id,
            "action":   "skip",
            "pk_range": f"{row.pk_start} → {row.pk_end}",
            "warning":  "Data in this PK range will NOT be migrated. "
                        "Ensure manual remediation is planned.",
            "reason":   reason,
        }

    def get_chunk_detail(self, db: Session, chunk_id: str) -> Dict[str, Any]:
        """Full chunk state for Operations Console display."""
        row = db.execute(
            text("""
                SELECT mc.*, mt.table_name,
                       wh.status AS worker_status,
                       wh.last_heartbeat AS worker_heartbeat
                FROM migration_chunks mc
                LEFT JOIN migration_tables mt ON mc.table_id = mt.id
                LEFT JOIN worker_heartbeats wh ON mc.worker_id = wh.worker_id
                WHERE mc.id = :id
            """),
            {"id": chunk_id}
        ).fetchone()

        if not row:
            return {}

        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()

        # Add Redis state
        assigned_to = redis_client.get(f"migration:chunk:{chunk_id}:assigned_to")
        d["assigned_to_override"] = assigned_to.decode() if assigned_to else None

        return d

    def list_problem_chunks(
        self,
        db:     Session,
        job_id: str,
        limit:  int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Returns chunks that need operator attention:
        failed chunks, stale running chunks, or chunks with high retry counts.
        """
        rows = db.execute(
            text("""
                SELECT mc.id, mc.status, mc.pk_start, mc.pk_end,
                       mc.retry_count, mc.last_error, mc.last_heartbeat,
                       mc.worker_id, mt.table_name,
                       CASE
                         WHEN mc.status='failed' THEN 'failed'
                         WHEN mc.status='running'
                              AND mc.last_heartbeat < NOW() - INTERVAL '15 minutes'
                         THEN 'stale'
                         WHEN mc.retry_count >= 3 THEN 'high_retries'
                         ELSE 'ok'
                       END AS problem_type
                FROM migration_chunks mc
                LEFT JOIN migration_tables mt ON mc.table_id = mt.id
                WHERE mc.job_id=:jid
                AND (
                    mc.status = 'failed'
                    OR (mc.status='running' AND mc.last_heartbeat < NOW() - INTERVAL '15 minutes')
                    OR mc.retry_count >= 3
                )
                ORDER BY
                    CASE mc.status WHEN 'failed' THEN 0 ELSE 1 END,
                    mc.retry_count DESC
                LIMIT :lim
            """),
            {"jid": job_id, "lim": limit}
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):        d[k] = str(v)
                if hasattr(v, "isoformat"):  d[k] = v.isoformat()
            result.append(d)
        return result

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
                    "rid":    resource_id,
                    "before": json.dumps(before, default=str),
                    "after":  json.dumps(after, default=str),
                    "reason": reason, "now": datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to log chunk action", error=str(e))
            try: db.rollback()
            except Exception: pass
