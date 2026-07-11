"""
CDC Router
File: migration/backend/connector_framework/routers/cdc.py

Endpoints:
    POST /cdc/sessions                      → create CDC session for a job
    GET  /cdc/sessions/{id}                 → get session status
    POST /cdc/sessions/{id}/start           → start capturing changes
    POST /cdc/sessions/{id}/stop            → stop capture
    GET  /cdc/sessions/{id}/stats           → events captured/replayed/pending
    POST /cdc/sessions/{id}/replay          → replay all pending events to target
    POST /cdc/sessions/{id}/replay-until-lag → replay until lag < threshold
    POST /cdc/sessions/{id}/cutover         → execute cutover (manual or automated)
    POST /cdc/sessions/{id}/complete        → complete manual cutover
    GET  /cdc/sessions/{id}/cutover-log     → step-by-step cutover log
    GET  /cdc/sessions/{id}/lag             → current replication lag in seconds
"""

import uuid
import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.database import get_db, engine as db_engine
from backend.cdc_engine.capture.cdc_capture import CDCCaptureEngine
from backend.cdc_engine.replay.cdc_replay import CDCReplayEngine
from backend.cdc_engine.cutover.cdc_cutover import CDCCutoverEngine

router = APIRouter(prefix="/cdc", tags=["CDC Engine"])

# In-memory capture engine registry (per process)
_capture_engines: Dict[str, CDCCaptureEngine] = {}


def _db_factory():
    """Returns a new DB session. Used by CDC engines."""
    from backend.shared.config.database import SessionLocal
    return SessionLocal()


class CreateSessionRequest(BaseModel):
    job_id:        str
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    tables:        List[str]


class StartCaptureRequest(BaseModel):
    tables: Optional[List[str]] = None


class ReplayUntilLagRequest(BaseModel):
    max_lag_seconds:  int = 5
    max_wait_minutes: int = 60


class CutoverRequest(BaseModel):
    tables:           List[str]
    mode:             str = "manual"   # manual | automated
    max_lag_seconds:  int = 5
    max_wait_minutes: int = 60


@router.post("/sessions", summary="Create a CDC session for a migration job")
def create_session(req: CreateSessionRequest, db: Session = Depends(get_db)):
    """
    Create a CDC session to enable near-zero-downtime migration.

    Full CDC flow:
      1. POST /cdc/sessions                → create session
      2. GET  position from session        → record start position
      3. Start bulk workers (normal migration)
      4. POST /cdc/sessions/{id}/start     → start capturing changes in parallel
      5. (workers complete initial load)
      6. POST /cdc/sessions/{id}/replay    → replay events captured during load
      7. POST /cdc/sessions/{id}/cutover   → final cutover when lag is low
    """
    # Validate source supports CDC
    source_engine = req.source_config.get("engine", "")
    from backend.connector_framework.registry.connector_registry import ConnectorRegistry
    if not ConnectorRegistry.supports(source_engine, "cdc"):
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{source_engine}' does not support CDC. "
                   f"CDC requires MySQL (binlog) or PostgreSQL (WAL)."
        )

    session_id = str(uuid.uuid4())
    now        = datetime.datetime.utcnow()

    # Get current source position BEFORE bulk load starts
    try:
        capture_engine = CDCCaptureEngine(session_id, req.source_config, _db_factory)
        position       = capture_engine.get_start_position()
        binlog_file    = position.file
        binlog_pos     = position.position
        wal_lsn        = position.lsn
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to get CDC start position: {e}. "
                   f"Ensure binlog (MySQL) or WAL (PostgreSQL) is enabled."
        )

    import json
    db.execute(
        text("""
            INSERT INTO cdc_sessions
                (id, job_id, source_db_type, status, capture_method,
                 binlog_file, binlog_position, wal_lsn,
                 initial_load_done, events_captured, events_replayed, events_pending,
                 created_at, updated_at)
            VALUES
                (:id, :jid, :stype, 'initializing',
                 :method, :bfile, :bpos, :lsn,
                 FALSE, 0, 0, 0, :now, :now)
        """),
        {
            "id":     session_id,
            "jid":    req.job_id,
            "stype":  source_engine,
            "method": "binlog" if source_engine == "mysql" else "wal",
            "bfile":  binlog_file,
            "bpos":   binlog_pos,
            "lsn":    wal_lsn,
            "now":    now,
        }
    )
    db.commit()

    return {
        "session_id":    session_id,
        "job_id":        req.job_id,
        "status":        "initializing",
        "capture_method": "binlog" if source_engine == "mysql" else "wal",
        "start_position": {
            "file":     binlog_file,
            "position": binlog_pos,
            "lsn":      wal_lsn,
        },
        "next_step": "Start your bulk migration workers, then call POST /cdc/sessions/{id}/start",
    }


@router.get("/sessions/{session_id}", summary="Get CDC session status")
def get_session(session_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM cdc_sessions WHERE id=:id"),
        {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"CDC session {session_id} not found")
    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d


@router.post("/sessions/{session_id}/start", summary="Start CDC capture")
def start_capture(
    session_id: str,
    req:        StartCaptureRequest,
    background: BackgroundTasks,
    db:         Session = Depends(get_db),
):
    """
    Start capturing changes from the source database in the background.
    Call this AFTER starting bulk migration workers.
    Changes will accumulate in cdc_events table for later replay.
    """
    row = db.execute(
        text("SELECT * FROM cdc_sessions WHERE id=:id"),
        {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    session = dict(row._mapping)
    source_config_str = db.execute(
        text("SELECT source_config FROM migration_jobs WHERE id=:jid"),
        {"jid": str(session["job_id"])}
    ).fetchone()

    # Get source config from job
    import json
    if source_config_str and source_config_str[0]:
        sc = source_config_str[0]
        if isinstance(sc, str):
            sc = json.loads(sc)
        source_config = sc
    else:
        raise HTTPException(status_code=400, detail="Cannot find source_config for this job")

    position_obj = None
    from backend.connector_framework.base.base_connector import CDCPosition
    if session.get("binlog_file"):
        position_obj = CDCPosition(
            method="binlog",
            file=session["binlog_file"],
            position=session["binlog_position"]
        )
    elif session.get("wal_lsn"):
        position_obj = CDCPosition(method="wal", lsn=session["wal_lsn"])
    else:
        raise HTTPException(status_code=400, detail="No start position recorded for this session")

    tables = req.tables or []
    engine = CDCCaptureEngine(session_id, source_config, _db_factory)
    _capture_engines[session_id] = engine

    background.add_task(engine.start, tables, position_obj)

    return {
        "session_id": session_id,
        "status":     "capturing",
        "tables":     tables,
        "message":    "CDC capture started in background. Events are accumulating in cdc_events.",
    }


@router.post("/sessions/{session_id}/stop", summary="Stop CDC capture")
def stop_capture(session_id: str):
    engine = _capture_engines.get(session_id)
    if not engine:
        return {"session_id": session_id, "message": "No active capture engine found"}
    count = engine.stop()
    del _capture_engines[session_id]
    return {"session_id": session_id, "events_captured": count, "status": "stopped"}


@router.get("/sessions/{session_id}/stats", summary="Get CDC event statistics")
def get_stats(session_id: str, db: Session = Depends(get_db)):
    """Returns events captured, replayed, pending, and current lag."""
    engine = _capture_engines.get(session_id)
    if engine:
        return engine.get_stats()

    replay = CDCReplayEngine(session_id, {}, _db_factory)
    return replay.get_stats()


@router.get("/sessions/{session_id}/lag", summary="Get current replication lag")
def get_lag(session_id: str, db: Session = Depends(get_db)):
    """
    Returns lag in seconds between last captured event and now.
    When this is near 0, the system is ready for cutover.
    """
    row = db.execute(
        text("""
            SELECT events_pending,
                   EXTRACT(EPOCH FROM (NOW() - last_captured_at))::INT AS lag_sec
            FROM cdc_sessions WHERE id=:id
        """),
        {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id":     session_id,
        "events_pending": row[0] or 0,
        "lag_seconds":    row[1] or 0,
        "cutover_ready":  (row[0] or 0) == 0 and (row[1] or 999) < 10,
    }


@router.post("/sessions/{session_id}/replay", summary="Replay all pending CDC events to target")
def replay_events(session_id: str, db: Session = Depends(get_db)):
    """
    Replay all captured CDC events to the target database.
    Call this after bulk migration workers complete.
    """
    row = db.execute(
        text("SELECT job_id FROM cdc_sessions WHERE id=:id"), {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    import json
    job = db.execute(
        text("SELECT target_config FROM migration_jobs WHERE id=:jid"),
        {"jid": str(row[0])}
    ).fetchone()
    target_config = json.loads(job[0]) if job and job[0] else {}

    replay = CDCReplayEngine(session_id, target_config, _db_factory)
    result = replay.replay_all()
    return {"session_id": session_id, **result}


@router.post("/sessions/{session_id}/replay-until-lag",
             summary="Replay events until replication lag drops below threshold")
def replay_until_lag(
    session_id: str,
    req:        ReplayUntilLagRequest,
    db:         Session = Depends(get_db),
):
    """
    Continuously replay events until lag < max_lag_seconds.
    Call this just before cutover to get target near real-time.
    """
    row = db.execute(
        text("SELECT job_id FROM cdc_sessions WHERE id=:id"), {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    import json
    job = db.execute(
        text("SELECT target_config FROM migration_jobs WHERE id=:jid"),
        {"jid": str(row[0])}
    ).fetchone()
    target_config = json.loads(job[0]) if job and job[0] else {}

    replay = CDCReplayEngine(session_id, target_config, _db_factory)
    result = replay.replay_until_lag(
        max_lag_seconds=req.max_lag_seconds,
        max_wait_seconds=req.max_wait_minutes * 60,
    )
    return {"session_id": session_id, **result}


@router.post("/sessions/{session_id}/cutover", summary="Execute migration cutover")
def execute_cutover(
    session_id: str,
    req:        CutoverRequest,
    db:         Session = Depends(get_db),
):
    """
    Execute the final cutover from source to target.

    mode=manual (default):
      Prepares everything, waits until lag is low, then PAUSES.
      Returns status=cutover_ready.
      You then stop application writes and call POST /sessions/{id}/complete.
      Downtime = time between stopping writes and calling /complete (~seconds).

    mode=automated:
      Runs all steps automatically including draining source connections.
      Use only when you have full control over the application layer.
    """
    import json
    row = db.execute(
        text("SELECT job_id FROM cdc_sessions WHERE id=:id"), {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    job = db.execute(
        text("SELECT source_config, target_config FROM migration_jobs WHERE id=:jid"),
        {"jid": str(row[0])}
    ).fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Migration job not found")

    source_config = json.loads(job[0]) if isinstance(job[0], str) else (job[0] or {})
    target_config = json.loads(job[1]) if isinstance(job[1], str) else (job[1] or {})

    cutover = CDCCutoverEngine(session_id, source_config, target_config, _db_factory)
    result  = cutover.execute(
        tables=req.tables,
        mode=req.mode,
        max_lag_seconds=req.max_lag_seconds,
        max_wait_minutes=req.max_wait_minutes,
    )
    return {"session_id": session_id, **result}


@router.post("/sessions/{session_id}/complete", summary="Complete manual cutover")
def complete_cutover(
    session_id: str,
    tables:     List[str],
    db:         Session = Depends(get_db),
):
    """
    Call this after stopping writes on the source application
    to complete the manual cutover (final replay + validate + mark done).
    """
    import json
    row = db.execute(
        text("SELECT job_id FROM cdc_sessions WHERE id=:id"), {"id": session_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    job = db.execute(
        text("SELECT source_config, target_config FROM migration_jobs WHERE id=:jid"),
        {"jid": str(row[0])}
    ).fetchone()
    source_config = json.loads(job[0]) if isinstance(job[0], str) else (job[0] or {})
    target_config = json.loads(job[1]) if isinstance(job[1], str) else (job[1] or {})

    cutover = CDCCutoverEngine(session_id, source_config, target_config, _db_factory)
    result  = cutover.complete_manual_cutover(tables=tables)
    return {"session_id": session_id, **result}


@router.get("/sessions/{session_id}/cutover-log", summary="Get cutover step log")
def get_cutover_log(session_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT step, status, details, started_at, completed_at
            FROM cutover_log WHERE session_id=:id ORDER BY created_at
        """),
        {"id": session_id}
    ).fetchall()
    steps = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
        steps.append(d)
    return {"session_id": session_id, "steps": steps}
