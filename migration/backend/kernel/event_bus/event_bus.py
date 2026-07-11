"""
Event Bus
File: migration/backend/kernel/event_bus/event_bus.py

Lightweight publish/subscribe system backed by Redis Pub/Sub, with a durable
record of every event written to the event_log table.

Why this exists:
    Right now, services that need to react to something (Notification Service
    needs to know a job failed; future Knowledge Base needs to know a job
    completed; future Schema Drift Detector needs to tell the Workflow Engine
    to pause) would otherwise require direct HTTP calls between every pair of
    services — N×N coupling.

    With the Event Bus: a service PUBLISHES "job.failed" once. Any number of
    services can SUBSCRIBE to "job.failed" or "job.*" independently. Publisher
    never needs to know who's listening.

Two delivery mechanisms combined:
    1. Redis Pub/Sub — real-time, in-memory, fire-and-forget. Fast. Lost if
       no subscriber is listening at publish time.
    2. event_log table — durable. Every publish() call also INSERTs a row.
       Services that were offline can call replay_since() to catch up.

Event naming convention: "<resource>.<action>"
    job.created, job.started, job.completed, job.failed, job.cancelled
    chunk.started, chunk.completed, chunk.failed, chunk.retrying
    drift.detected, drift.resolved
    validation.started, validation.passed, validation.failed
    approval.requested, approval.granted, approval.rejected
    worker.started, worker.stopped, worker.paused
    cdc.capture_started, cdc.lag_low, cdc.cutover_ready

Usage:
    from backend.kernel.event_bus.event_bus import EventBus

    # Publish (from any service)
    EventBus.publish(
        event_type="job.failed",
        source_service="worker_service",
        resource_type="job",
        resource_id=str(job_id),
        payload={"error": "connection timeout", "chunk_id": str(chunk_id)},
        correlation_id=str(job_id),
    )

    # Subscribe (blocking — run in a background thread)
    def on_job_failed(event):
        print(f"Job {event['resource_id']} failed: {event['payload']}")

    EventBus.subscribe(["job.failed", "job.cancelled"], on_job_failed)
"""

import json
import uuid
import datetime
import threading
import fnmatch
from typing import Dict, Any, List, Optional, Callable
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


REDIS_CHANNEL_PREFIX = "migration_events"


class EventBus:

    _subscriber_threads: List[threading.Thread] = []

    # ── Publish ───────────────────────────────────────────────────────────────

    @classmethod
    def publish(
        cls,
        event_type:     str,
        source_service: str,
        resource_type:  Optional[str] = None,
        resource_id:    Optional[str] = None,
        payload:        Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        tenant_id:      str = "local",
        db:             Optional[Session] = None,
    ) -> str:
        """
        Publish an event. Writes to event_log (durable) AND publishes to
        Redis Pub/Sub (real-time). Returns the event ID.

        If db session not provided, opens and closes its own — publish()
        should never be a reason a caller's transaction fails, so DB write
        failures here are logged but swallowed (event still goes out via Redis).
        """
        event_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow()

        event_envelope = {
            "id":             event_id,
            "event_type":     event_type,
            "source_service": source_service,
            "resource_type":  resource_type,
            "resource_id":    resource_id,
            "payload":        payload or {},
            "correlation_id": correlation_id,
            "tenant_id":      tenant_id,
            "published_at":   now.isoformat(),
        }

        # 1. Durable write to event_log
        cls._write_log(event_envelope, db)

        # 2. Real-time publish to Redis Pub/Sub
        try:
            channel = f"{REDIS_CHANNEL_PREFIX}:{event_type}"
            redis_client.publish(channel, json.dumps(event_envelope))
            # Also publish to a wildcard-friendly catch-all channel so
            # subscribers using patterns like "job.*" can listen on one channel
            redis_client.publish(f"{REDIS_CHANNEL_PREFIX}:all", json.dumps(event_envelope))
        except Exception as e:
            logger.warning("Event Bus Redis publish failed (event still logged)",
                           event_type=event_type, error=str(e))

        logger.debug("Event published", event_type=event_type,
                    resource_id=resource_id, event_id=event_id)
        return event_id

    @classmethod
    def _write_log(cls, envelope: Dict[str, Any], db: Optional[Session]) -> None:
        owns_session = db is None
        if owns_session:
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()
        try:
            db.execute(
                text("""
                    INSERT INTO event_log
                        (id, tenant_id, event_type, source_service,
                         resource_type, resource_id, payload, correlation_id, published_at)
                    VALUES
                        (:id, :tid, :etype, :svc,
                         :rtype, :rid, :payload::jsonb, :corr, :now)
                """),
                {
                    "id":      envelope["id"],
                    "tid":     envelope["tenant_id"],
                    "etype":   envelope["event_type"],
                    "svc":     envelope["source_service"],
                    "rtype":   envelope["resource_type"],
                    "rid":     envelope["resource_id"],
                    "payload": json.dumps(envelope["payload"]),
                    "corr":    envelope["correlation_id"],
                    "now":     datetime.datetime.fromisoformat(envelope["published_at"]),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Event Bus log write failed", error=str(e))
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            if owns_session:
                db.close()

    # ── Subscribe ─────────────────────────────────────────────────────────────

    @classmethod
    def subscribe(
        cls,
        patterns: List[str],
        callback: Callable[[Dict[str, Any]], None],
        run_in_background: bool = True,
    ) -> Optional[threading.Thread]:
        """
        Subscribe to one or more event type patterns (supports fnmatch-style
        wildcards: "job.*", "*.failed", "*").

        callback receives the full event envelope dict.

        If run_in_background=True (default), starts a daemon thread and
        returns it immediately (non-blocking) — typical usage in a service's
        startup code. If False, blocks the calling thread forever (useful
        for a dedicated worker process whose only job is consuming events).
        """
        def _listen():
            pubsub = redis_client.pubsub()
            # Subscribe to the catch-all channel; filter by pattern in Python.
            # (Redis PSUBSCRIBE on raw event_type would also work, but using
            # one channel + Python-side fnmatch keeps channel count low and
            # makes multi-pattern subscriptions trivial.)
            pubsub.subscribe(f"{REDIS_CHANNEL_PREFIX}:all")

            logger.info("Event Bus subscriber started", patterns=patterns)

            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except Exception:
                    continue

                event_type = event.get("event_type", "")
                if any(fnmatch.fnmatch(event_type, p) for p in patterns):
                    try:
                        callback(event)
                    except Exception as e:
                        logger.error(
                            "Event Bus subscriber callback failed",
                            event_type=event_type, error=str(e)
                        )

        if run_in_background:
            thread = threading.Thread(target=_listen, daemon=True, name="event-bus-subscriber")
            thread.start()
            cls._subscriber_threads.append(thread)
            return thread
        else:
            _listen()
            return None

    @classmethod
    def register_subscription(
        cls,
        db:              Session,
        subscriber_name: str,
        event_pattern:   str,
        handler_path:    Optional[str] = None,
    ) -> dict:
        """
        Record (for introspection only) that a subscriber is interested in
        a pattern. Doesn't itself wire up delivery — call subscribe() for that.
        Useful so Operations Console / Frontend can show "who listens to what."
        """
        sid = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO event_subscriptions
                    (id, subscriber_name, event_pattern, handler_path, is_active, created_at)
                VALUES (:id, :name, :pattern, :handler, TRUE, :now)
            """),
            {
                "id": sid, "name": subscriber_name, "pattern": event_pattern,
                "handler": handler_path, "now": datetime.datetime.utcnow(),
            }
        )
        db.commit()
        return {"subscription_id": sid, "subscriber": subscriber_name, "pattern": event_pattern}

    # ── Replay / Query ────────────────────────────────────────────────────────

    @classmethod
    def replay_since(
        cls,
        db:             Session,
        since:          datetime.datetime,
        event_types:    Optional[List[str]] = None,
        correlation_id: Optional[str] = None,
        limit:          int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical events from event_log — used by a service that was
        offline and needs to catch up, or by Knowledge Base / Operations
        Console to reconstruct "what happened" for a given job.
        """
        conditions = ["published_at >= :since"]
        params: Dict[str, Any] = {"since": since, "lim": limit}

        if event_types:
            conditions.append("event_type = ANY(:etypes)")
            params["etypes"] = event_types
        if correlation_id:
            conditions.append("correlation_id = :corr")
            params["corr"] = correlation_id

        where = " AND ".join(conditions)

        rows = db.execute(
            text(f"""
                SELECT id, tenant_id, event_type, source_service,
                       resource_type, resource_id, payload, correlation_id, published_at
                FROM event_log
                WHERE {where}
                ORDER BY published_at ASC
                LIMIT :lim
            """),
            params
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "hex"):       d[k] = str(v)
                if hasattr(v, "isoformat"): d[k] = v.isoformat()
            result.append(d)
        return result

    @classmethod
    def get_timeline(cls, db: Session, correlation_id: str) -> List[Dict[str, Any]]:
        """
        Convenience: full event timeline for one correlation_id (typically a
        job_id) — exactly what the Operations Console / Job Monitor UI needs
        to render "everything that happened to this job."
        """
        return cls.replay_since(
            db=db,
            since=datetime.datetime(2020, 1, 1),
            correlation_id=correlation_id,
            limit=5000,
        )
