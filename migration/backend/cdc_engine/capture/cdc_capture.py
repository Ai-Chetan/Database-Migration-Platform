"""
CDC Capture Engine
File: migration/backend/cdc_engine/capture/cdc_capture.py

Change Data Capture — captures every INSERT/UPDATE/DELETE on the source
database that happens DURING the initial bulk migration load and stores
them as CDC events for later replay.

Why this matters:
    Initial load of 5TB takes 18 hours.
    During those 18 hours, users continue changing data.
    Without CDC: target is 18 hours stale at cutover.
    With CDC:    target has every change replayed — near-zero data loss.

Flow:
    1. Record source DB position BEFORE initial load starts
    2. Run initial bulk load (workers migrate existing data)
    3. CDC capture runs in parallel, saving all new changes to cdc_events
    4. After bulk load completes: replay captured events to target
    5. When lag drops to near-zero: ready for cutover

Capture methods:
    MySQL:      Binary log (binlog) via mysql-replication library
    PostgreSQL: Write-Ahead Log (WAL) via logical replication

Usage:
    engine = CDCCaptureEngine(session_id, source_config, db)
    engine.start(tables=["users","orders"])   # runs in background thread
    # ... initial load happens ...
    engine.stop()
"""

import threading
import datetime
import uuid
import json
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.connector_framework.base.base_connector import CDCPosition
from backend.shared.config.logging import logger


class CDCCaptureEngine:

    def __init__(
        self,
        session_id:    str,
        source_config: dict,
        db_factory,          # callable returning a DB Session
    ):
        self.session_id    = session_id
        self.source_config = source_config
        self.db_factory    = db_factory
        self._thread: Optional[threading.Thread] = None
        self._running      = False
        self._connector    = None
        self._events_captured = 0

    def get_start_position(self) -> CDCPosition:
        """
        Get the current source DB position BEFORE starting the bulk load.
        This is the starting point for CDC — we capture everything AFTER this.
        Call this before starting workers.
        """
        connector = ConnectorRegistry.get_for_config(self.source_config)
        connector.connect()
        try:
            position = connector.get_cdc_position()
            logger.info(
                "CDC start position recorded",
                session_id=self.session_id,
                method=position.method,
                file=position.file,
                position=position.position,
                lsn=position.lsn,
            )
            return position
        finally:
            connector.disconnect()

    def start(
        self,
        tables:   List[str],
        position: CDCPosition,
    ) -> None:
        """
        Start the CDC capture in a background thread.
        All events captured are written to cdc_events table.
        """
        if self._running:
            logger.warning("CDC capture already running", session_id=self.session_id)
            return

        self._running = True
        self._update_session_status("capturing")

        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(tables, position),
            daemon=True,
            name=f"cdc-capture-{self.session_id[:8]}"
        )
        self._thread.start()
        logger.info("CDC capture started", session_id=self.session_id, tables=tables)

    def stop(self) -> int:
        """
        Stop CDC capture. Returns the number of events captured.
        """
        self._running = False
        if self._connector:
            try:
                self._connector.stop_cdc_capture()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=30)

        self._update_session_status("paused")
        logger.info(
            "CDC capture stopped",
            session_id=self.session_id,
            events_captured=self._events_captured
        )
        return self._events_captured

    def get_lag(self) -> int:
        """
        Returns approximate replication lag in seconds.
        When this approaches 0, the system is ready for cutover.
        """
        db = self.db_factory()
        try:
            row = db.execute(
                text("""
                    SELECT
                        events_captured - events_replayed AS pending,
                        EXTRACT(EPOCH FROM (NOW() - last_captured_at)) AS lag_sec
                    FROM cdc_sessions WHERE id = :sid
                """),
                {"sid": self.session_id}
            ).fetchone()
            return int(row[1] or 0) if row else 0
        finally:
            db.close()

    def get_stats(self) -> dict:
        """Get current capture statistics."""
        db = self.db_factory()
        try:
            row = db.execute(
                text("""
                    SELECT status, events_captured, events_replayed, events_pending,
                           lag_seconds, last_captured_at, capture_started_at
                    FROM cdc_sessions WHERE id = :sid
                """),
                {"sid": self.session_id}
            ).fetchone()
            if not row:
                return {}
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "isoformat"): d[k] = v.isoformat()
            return d
        finally:
            db.close()

    # ── Private ───────────────────────────────────────────────────────────────

    def _capture_loop(self, tables: List[str], position: CDCPosition) -> None:
        """Main capture loop running in background thread."""
        try:
            self._connector = ConnectorRegistry.get_for_config(self.source_config)
            self._connector.connect()

            self._update_capture_start(position)

            self._connector.start_cdc_capture(
                tables=tables,
                position=position,
                callback=self._on_event,
            )
        except Exception as e:
            logger.error("CDC capture loop failed", session_id=self.session_id, error=str(e))
            self._update_session_status("failed", error=str(e))
        finally:
            if self._connector:
                self._connector.disconnect()

    def _on_event(
        self,
        event_type:   str,
        table_name:   str,
        before_image: Optional[dict],
        after_image:  Optional[dict],
        position:     CDCPosition,
    ) -> None:
        """
        Called by the connector for every captured change event.
        Writes the event to cdc_events table for later replay.
        """
        if not self._running:
            return

        db = self.db_factory()
        try:
            # Extract PK values for the row
            pk_values = {}
            image = after_image or before_image or {}
            if image:
                pk_values = {k: str(v) for k, v in image.items() if "id" in k.lower()}

            # Store position string
            pos_str = position.file + ":" + str(position.position) if position.file else position.lsn

            db.execute(
                text("""
                    INSERT INTO cdc_events
                        (id, session_id, event_type, table_name,
                         event_position, before_image, after_image,
                         pk_values, replayed, captured_at)
                    VALUES
                        (:id, :sid, :etype, :tname,
                         :pos, :before::jsonb, :after::jsonb,
                         :pk::jsonb, FALSE, :now)
                """),
                {
                    "id":     str(uuid.uuid4()),
                    "sid":    self.session_id,
                    "etype":  event_type,
                    "tname":  table_name,
                    "pos":    pos_str,
                    "before": json.dumps(before_image) if before_image else None,
                    "after":  json.dumps(after_image) if after_image else None,
                    "pk":     json.dumps(pk_values),
                    "now":    datetime.datetime.utcnow(),
                }
            )
            db.commit()

            self._events_captured += 1

            # Update session stats every 100 events
            if self._events_captured % 100 == 0:
                self._update_session_counts(db)

        except Exception as e:
            logger.warning("Failed to store CDC event", error=str(e))
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    def _update_session_status(self, status: str, error: str = None) -> None:
        db = self.db_factory()
        try:
            db.execute(
                text("""
                    UPDATE cdc_sessions SET status=:s, error_message=:err, updated_at=:now
                    WHERE id=:sid
                """),
                {"s": status, "err": error, "now": datetime.datetime.utcnow(), "sid": self.session_id}
            )
            db.commit()
        finally:
            db.close()

    def _update_capture_start(self, position: CDCPosition) -> None:
        db = self.db_factory()
        try:
            db.execute(
                text("""
                    UPDATE cdc_sessions SET
                        capture_started_at = :now,
                        binlog_file        = :file,
                        binlog_position    = :pos,
                        wal_lsn            = :lsn,
                        updated_at         = :now
                    WHERE id = :sid
                """),
                {
                    "now":  datetime.datetime.utcnow(),
                    "file": position.file,
                    "pos":  position.position,
                    "lsn":  position.lsn,
                    "sid":  self.session_id,
                }
            )
            db.commit()
        finally:
            db.close()

    def _update_session_counts(self, db: Session) -> None:
        db.execute(
            text("""
                UPDATE cdc_sessions SET
                    events_captured  = (SELECT COUNT(*) FROM cdc_events WHERE session_id=:sid),
                    events_pending   = (SELECT COUNT(*) FROM cdc_events WHERE session_id=:sid AND replayed=FALSE),
                    last_captured_at = :now,
                    updated_at       = :now
                WHERE id = :sid
            """),
            {"sid": self.session_id, "now": datetime.datetime.utcnow()}
        )
        db.commit()
