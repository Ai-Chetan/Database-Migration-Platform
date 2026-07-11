"""
CDC Replay Engine
File: migration/backend/cdc_engine/replay/cdc_replay.py

Replays captured CDC events (from cdc_events table) onto the target database.

After the initial bulk load completes, this engine processes all the
INSERT/UPDATE/DELETE events that accumulated during the load and applies
them to the target in chronological order.

The replay engine runs continuously until:
    - All events are replayed AND
    - The lag (time since last new event) is small enough for cutover

Replay strategy per event type:
    INSERT → bulk_insert with mode=upsert (handles cases where row might exist)
    UPDATE → UPDATE target WHERE pk = event_pk
    DELETE → DELETE FROM target WHERE pk = event_pk

Idempotency:
    Events are marked replayed=TRUE after successful application.
    On restart, only unreplayed events are processed.
    Safe to re-run.

Usage:
    engine = CDCReplayEngine(session_id, target_config, db_factory)
    engine.replay_all()    # replay everything captured so far
    engine.replay_until_lag(max_lag_seconds=5)  # replay until near-realtime
"""

import json
import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.shared.config.logging import logger


class CDCReplayEngine:

    BATCH_SIZE = 500   # Events to replay in one batch

    def __init__(
        self,
        session_id:    str,
        target_config: dict,
        db_factory,
    ):
        self.session_id    = session_id
        self.target_config = target_config
        self.db_factory    = db_factory
        self._replayed     = 0
        self._errors       = 0

    def replay_all(self) -> Dict[str, int]:
        """
        Replay all unreplayed CDC events for this session.
        Returns {"replayed": N, "errors": N}
        """
        logger.info("CDC replay starting", session_id=self.session_id)
        connector = ConnectorRegistry.get_for_config(self.target_config)
        connector.connect()

        try:
            while True:
                batch = self._fetch_unreplayed_batch()
                if not batch:
                    break
                self._apply_batch(connector, batch)

            self._update_session(connector)
            logger.info(
                "CDC replay complete",
                session_id=self.session_id,
                replayed=self._replayed,
                errors=self._errors
            )
        finally:
            connector.disconnect()

        return {"replayed": self._replayed, "errors": self._errors}

    def replay_until_lag(
        self,
        max_lag_seconds:  int = 5,
        poll_interval:    int = 2,
        max_wait_seconds: int = 3600,
    ) -> Dict[str, Any]:
        """
        Replay events continuously until the lag drops below max_lag_seconds.
        Used right before cutover to ensure target is nearly in sync.

        Returns when lag <= max_lag_seconds or max_wait_seconds exceeded.
        """
        import time
        logger.info(
            "CDC continuous replay until lag",
            session_id=self.session_id,
            target_lag_sec=max_lag_seconds
        )

        connector = ConnectorRegistry.get_for_config(self.target_config)
        connector.connect()
        start = time.time()

        try:
            while True:
                elapsed = time.time() - start
                if elapsed > max_wait_seconds:
                    logger.warning(
                        "CDC replay timed out waiting for low lag",
                        session_id=self.session_id,
                        elapsed=int(elapsed)
                    )
                    return {"ready": False, "reason": "timeout", "replayed": self._replayed}

                # Replay any pending events
                batch = self._fetch_unreplayed_batch()
                if batch:
                    self._apply_batch(connector, batch)

                # Check lag
                lag = self._get_lag_seconds()
                pending = self._count_pending()

                logger.info(
                    "CDC lag check",
                    lag_sec=lag,
                    pending_events=pending,
                    replayed_total=self._replayed
                )

                if pending == 0 and lag <= max_lag_seconds:
                    logger.info(
                        "CDC lag within threshold — ready for cutover",
                        lag_sec=lag,
                        session_id=self.session_id
                    )
                    return {
                        "ready":    True,
                        "lag_sec":  lag,
                        "replayed": self._replayed,
                        "pending":  pending,
                    }

                time.sleep(poll_interval)
        finally:
            connector.disconnect()
            self._update_session(connector)

    def get_stats(self) -> Dict[str, Any]:
        db = self.db_factory()
        try:
            row = db.execute(
                text("""
                    SELECT events_captured, events_replayed, events_pending, lag_seconds
                    FROM cdc_sessions WHERE id = :sid
                """),
                {"sid": self.session_id}
            ).fetchone()
            return dict(row._mapping) if row else {}
        finally:
            db.close()

    # ── Private ───────────────────────────────────────────────────────────────

    def _fetch_unreplayed_batch(self) -> List[Dict]:
        db = self.db_factory()
        try:
            rows = db.execute(
                text("""
                    SELECT id, event_type, table_name,
                           before_image, after_image, pk_values
                    FROM cdc_events
                    WHERE session_id = :sid AND replayed = FALSE
                    ORDER BY captured_at ASC
                    LIMIT :lim
                """),
                {"sid": self.session_id, "lim": self.BATCH_SIZE}
            ).fetchall()

            result = []
            for row in rows:
                d = dict(row._mapping)
                # Parse JSON fields
                for f in ("before_image", "after_image", "pk_values"):
                    if isinstance(d.get(f), str):
                        try:
                            d[f] = json.loads(d[f])
                        except Exception:
                            d[f] = {}
                if hasattr(d.get("id"), "hex"):
                    d["id"] = str(d["id"])
                result.append(d)
            return result
        finally:
            db.close()

    def _apply_batch(self, connector, events: List[Dict]) -> None:
        """Apply a batch of CDC events to the target database."""
        succeeded_ids = []
        failed_ids    = []

        # Group INSERTs by table for efficient bulk processing
        insert_groups: Dict[str, List] = {}
        other_events  = []

        for event in events:
            if event["event_type"] == "INSERT" and event.get("after_image"):
                insert_groups.setdefault(event["table_name"], []).append(event)
            else:
                other_events.append(event)

        # Bulk replay INSERTs
        for table_name, table_events in insert_groups.items():
            rows = [e["after_image"] for e in table_events if e.get("after_image")]
            if rows:
                try:
                    connector.bulk_insert(table_name, rows, mode="upsert")
                    succeeded_ids.extend(e["id"] for e in table_events)
                    self._replayed += len(rows)
                except Exception as e:
                    logger.error("CDC INSERT batch failed",
                                 table=table_name, error=str(e))
                    failed_ids.extend(e["id"] for e in table_events)
                    self._errors += len(table_events)

        # Individual replay for UPDATE and DELETE
        for event in other_events:
            try:
                self._apply_single_event(connector, event)
                succeeded_ids.append(event["id"])
                self._replayed += 1
            except Exception as e:
                logger.error("CDC event replay failed",
                             event_type=event["event_type"],
                             table=event["table_name"], error=str(e))
                failed_ids.append(event["id"])
                self._errors += 1

        # Mark succeeded events as replayed
        if succeeded_ids:
            self._mark_replayed(succeeded_ids)

        if failed_ids:
            self._mark_failed(failed_ids)

    def _apply_single_event(self, connector, event: Dict) -> None:
        """Apply one UPDATE or DELETE event to the target."""
        event_type  = event["event_type"]
        table_name  = event["table_name"]
        after_image = event.get("after_image") or {}
        before_image = event.get("before_image") or {}
        pk_values   = event.get("pk_values") or {}

        if event_type == "UPDATE" and after_image:
            # For UPDATE: use upsert (insert-or-replace)
            connector.bulk_insert(table_name, [after_image], mode="upsert")

        elif event_type == "DELETE" and (pk_values or before_image):
            # For DELETE: build and execute DELETE WHERE pk = value
            # Use connector's raw connection for this
            row_to_delete = pk_values or before_image
            if row_to_delete:
                self._execute_delete(connector, table_name, row_to_delete)

    def _execute_delete(self, connector, table_name: str, pk_row: Dict) -> None:
        """Execute a DELETE on the target for a specific row."""
        engine = self.target_config.get("engine", "mysql").lower()
        conn   = connector._connection
        if not conn:
            return

        # Build WHERE clause from PK values
        if engine in ("mysql", "mariadb"):
            where = " AND ".join(f"`{k}` = %s" for k in pk_row)
            sql   = f"DELETE FROM `{table_name}` WHERE {where}"
        else:
            where = " AND ".join(f'"{k}" = %s' for k in pk_row)
            sql   = f'DELETE FROM "{table_name}" WHERE {where}'

        values = list(pk_row.values())

        cursor = conn.cursor()
        try:
            cursor.execute(sql, values)
            conn.commit()
        finally:
            cursor.close()

    def _mark_replayed(self, event_ids: List[str]) -> None:
        db = self.db_factory()
        try:
            id_list = "','".join(event_ids)
            db.execute(
                text(f"""
                    UPDATE cdc_events
                    SET replayed=TRUE, replayed_at=:now
                    WHERE id IN ('{id_list}')
                """),
                {"now": datetime.datetime.utcnow()}
            )
            db.commit()
        finally:
            db.close()

    def _mark_failed(self, event_ids: List[str]) -> None:
        db = self.db_factory()
        try:
            id_list = "','".join(event_ids)
            db.execute(
                text(f"""
                    UPDATE cdc_events
                    SET replay_error='replay failed'
                    WHERE id IN ('{id_list}')
                """)
            )
            db.commit()
        finally:
            db.close()

    def _count_pending(self) -> int:
        db = self.db_factory()
        try:
            row = db.execute(
                text("SELECT COUNT(*) FROM cdc_events WHERE session_id=:sid AND replayed=FALSE"),
                {"sid": self.session_id}
            ).fetchone()
            return row[0] if row else 0
        finally:
            db.close()

    def _get_lag_seconds(self) -> int:
        db = self.db_factory()
        try:
            row = db.execute(
                text("""
                    SELECT EXTRACT(EPOCH FROM (NOW() - last_captured_at))
                    FROM cdc_sessions WHERE id=:sid
                """),
                {"sid": self.session_id}
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            db.close()

    def _update_session(self, connector) -> None:
        db = self.db_factory()
        try:
            db.execute(
                text("""
                    UPDATE cdc_sessions SET
                        events_replayed = (SELECT COUNT(*) FROM cdc_events WHERE session_id=:sid AND replayed=TRUE),
                        events_pending  = (SELECT COUNT(*) FROM cdc_events WHERE session_id=:sid AND replayed=FALSE),
                        last_replayed_at = :now,
                        updated_at       = :now
                    WHERE id=:sid
                """),
                {"sid": self.session_id, "now": datetime.datetime.utcnow()}
            )
            db.commit()
        finally:
            db.close()
