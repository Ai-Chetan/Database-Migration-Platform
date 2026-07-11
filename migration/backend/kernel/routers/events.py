"""
Event Bus Router
File: migration/backend/kernel/routers/events.py

Endpoints:
    POST /events/publish               → publish an event (mainly for testing/manual triggers)
    GET  /events/timeline/{correlation_id}  → full event history for one job/resource
    GET  /events/replay                → replay events since a timestamp
    POST /events/subscriptions         → register a subscription (introspection only)
    GET  /events/subscriptions         → list registered subscriptions
"""

import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from backend.shared.config.database import get_db
from backend.kernel.event_bus.event_bus import EventBus

router = APIRouter(prefix="/events", tags=["Event Bus"])


class PublishEventRequest(BaseModel):
    event_type:     str
    source_service: str
    resource_type:  Optional[str] = None
    resource_id:    Optional[str] = None
    payload:        Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    tenant_id:      str = "local"


class SubscribeRequest(BaseModel):
    subscriber_name: str
    event_pattern:   str
    handler_path:    Optional[str] = None


@router.post("/publish", summary="Publish an event onto the Event Bus")
def publish_event(req: PublishEventRequest, db: Session = Depends(get_db)):
    """
    Publish an event. Writes durably to event_log AND broadcasts via
    Redis Pub/Sub to any live subscribers.

    In normal operation, services call EventBus.publish() directly in
    Python (no HTTP round-trip needed since they're in the same codebase).
    This HTTP endpoint exists for: manual testing, triggering events from
    the Operations Console UI (Part 8/11), and cross-language integrations.

    Common event_type values already adopted by the convention:
      job.created, job.started, job.completed, job.failed, job.cancelled
      chunk.started, chunk.completed, chunk.failed, chunk.retrying
      drift.detected, drift.resolved
      validation.started, validation.passed, validation.failed
      approval.requested, approval.granted, approval.rejected
      worker.started, worker.stopped, worker.paused
      cdc.capture_started, cdc.lag_low, cdc.cutover_ready
    """
    event_id = EventBus.publish(
        event_type=req.event_type,
        source_service=req.source_service,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        payload=req.payload,
        correlation_id=req.correlation_id,
        tenant_id=req.tenant_id,
        db=db,
    )
    return {"event_id": event_id, "event_type": req.event_type, "status": "published"}


@router.get("/timeline/{correlation_id}", summary="Full event timeline for one job/resource")
def get_timeline(correlation_id: str, db: Session = Depends(get_db)):
    """
    Returns every event published with this correlation_id (typically a
    job_id), in chronological order. This is the data source for the
    Operations Console / Job Monitor UI's "activity timeline" view.
    """
    timeline = EventBus.get_timeline(db, correlation_id)
    return {"correlation_id": correlation_id, "total_events": len(timeline), "events": timeline}


@router.get("/replay", summary="Replay events since a timestamp")
def replay_events(
    since_iso:      str,
    event_types:    Optional[str] = None,   # comma-separated
    correlation_id: Optional[str] = None,
    limit:          int = 1000,
    db:             Session = Depends(get_db),
):
    """
    Fetch historical events from the durable log. Used by a service that
    was offline and needs to catch up on what it missed, or for debugging.

    since_iso: ISO timestamp, e.g. "2026-06-30T00:00:00"
    event_types: comma-separated list, e.g. "job.failed,chunk.failed"
    """
    try:
        since = datetime.datetime.fromisoformat(since_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="since_iso must be a valid ISO timestamp")

    types_list = event_types.split(",") if event_types else None

    events = EventBus.replay_since(
        db=db, since=since, event_types=types_list,
        correlation_id=correlation_id, limit=limit,
    )
    return {"since": since_iso, "total": len(events), "events": events}


@router.post("/subscriptions", summary="Register a subscription (for introspection)")
def register_subscription(req: SubscribeRequest, db: Session = Depends(get_db)):
    """
    Records that a subscriber is interested in an event pattern. This is
    introspection/documentation only — it does NOT wire up actual delivery.
    Actual subscription happens via EventBus.subscribe() in Python code
    within the subscribing service's process.

    Use this so the Operations Console can show "who listens to what."
    """
    return EventBus.register_subscription(
        db=db, subscriber_name=req.subscriber_name,
        event_pattern=req.event_pattern, handler_path=req.handler_path,
    )


@router.get("/subscriptions", summary="List registered subscriptions")
def list_subscriptions(db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, subscriber_name, event_pattern, handler_path, is_active, created_at
            FROM event_subscriptions ORDER BY subscriber_name
        """)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return result


@router.get("/types", summary="List distinct event types seen so far")
def list_event_types(db: Session = Depends(get_db)):
    """Returns every distinct event_type that has actually been published, with counts."""
    rows = db.execute(
        text("""
            SELECT event_type, COUNT(*) as count, MAX(published_at) as last_seen
            FROM event_log GROUP BY event_type ORDER BY count DESC
        """)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("last_seen"), "isoformat"):
            d["last_seen"] = d["last_seen"].isoformat()
        result.append(d)
    return result
