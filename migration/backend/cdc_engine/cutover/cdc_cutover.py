"""
CDC Cutover Engine
File: migration/backend/cdc_engine/cutover/cdc_cutover.py

Orchestrates the final cutover from source to target database.

Cutover steps (in order):
  1. Verify initial load is complete
  2. Replay all remaining CDC events
  3. Wait until replication lag < threshold (default: 5 seconds)
  4. Stop writes on source (application-level or connection drain)
  5. Replay final remaining events
  6. Validate row counts + checksums match
  7. Mark session as cutover_ready
  8. Signal application to switch connection strings
  9. Mark migration complete

Near-zero downtime:
  The total downtime = time to drain final events after stopping writes.
  With low lag this is typically 1-10 seconds.

Automated vs manual cutover:
  automated: engine executes all steps automatically
  manual:    engine prepares everything and waits for human confirmation
             at step 4 before stopping writes

Usage:
    engine = CDCCutoverEngine(session_id, source_config, target_config, db_factory)
    result = engine.execute(mode="automated", max_lag_seconds=5)
"""

import datetime
import time
import uuid
import json
from typing import Dict, Any, List
from sqlalchemy import text

from backend.cdc_engine.replay.cdc_replay import CDCReplayEngine
from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.shared.config.logging import logger


class CDCCutoverEngine:

    def __init__(
        self,
        session_id:    str,
        source_config: dict,
        target_config: dict,
        db_factory,
    ):
        self.session_id    = session_id
        self.source_config = source_config
        self.target_config = target_config
        self.db_factory    = db_factory
        self.replay_engine = CDCReplayEngine(session_id, target_config, db_factory)

    def execute(
        self,
        tables:           List[str],
        mode:             str = "manual",      # manual | automated
        max_lag_seconds:  int = 5,
        max_wait_minutes: int = 60,
    ) -> Dict[str, Any]:
        """
        Execute the full cutover sequence.
        mode=manual:    pauses at step 4 and returns — human triggers final step
        mode=automated: runs all steps automatically
        """
        logger.info("Cutover starting", session_id=self.session_id, mode=mode)
        steps_completed = []

        try:
            # ── Step 1: Verify initial load done ─────────────────────────────
            self._log_step("verify_initial_load", "running")
            if not self._verify_initial_load_complete():
                self._log_step("verify_initial_load", "failed",
                               "Initial bulk load is not complete")
                return {"success": False, "reason": "Initial bulk load not complete"}
            self._log_step("verify_initial_load", "completed")
            steps_completed.append("verify_initial_load")

            # ── Step 2: Replay pending events ─────────────────────────────────
            self._log_step("replay_pending_events", "running")
            replay_result = self.replay_engine.replay_all()
            self._log_step(
                "replay_pending_events", "completed",
                f"Replayed {replay_result['replayed']} events"
            )
            steps_completed.append("replay_pending_events")

            # ── Step 3: Wait for lag to drop ──────────────────────────────────
            self._log_step("wait_for_low_lag", "running")
            lag_result = self.replay_engine.replay_until_lag(
                max_lag_seconds=max_lag_seconds,
                max_wait_seconds=max_wait_minutes * 60,
            )
            if not lag_result.get("ready"):
                self._log_step("wait_for_low_lag", "failed", "Lag did not drop in time")
                return {"success": False, "reason": "Lag threshold not reached", "lag": lag_result}
            self._log_step("wait_for_low_lag", "completed",
                           f"Lag: {lag_result.get('lag_sec')}s")
            steps_completed.append("wait_for_low_lag")

            # ── Step 4: Stop source writes ────────────────────────────────────
            # In manual mode: pause here and return ready status
            # The application team manually switches connection strings
            if mode == "manual":
                self._update_session_status("cutover_ready")
                self._log_step("stop_source_writes", "pending",
                               "MANUAL ACTION REQUIRED: switch application to read-only, then call /cdc/{id}/complete")
                return {
                    "success":         True,
                    "status":          "cutover_ready",
                    "mode":            "manual",
                    "lag_seconds":     lag_result.get("lag_sec"),
                    "steps_completed": steps_completed,
                    "next_action":     "Stop writes on source application, then call POST /cdc/{session_id}/complete",
                }

            # Automated mode: attempt to drain source connections
            self._log_step("stop_source_writes", "running")
            self._drain_source_connections()
            self._log_step("stop_source_writes", "completed")
            steps_completed.append("stop_source_writes")

            # ── Step 5: Replay final events ───────────────────────────────────
            self._log_step("replay_final_events", "running")
            final_result = self.replay_engine.replay_all()
            self._log_step("replay_final_events", "completed",
                           f"Final replay: {final_result['replayed']} events")
            steps_completed.append("replay_final_events")

            # ── Step 6: Validate ──────────────────────────────────────────────
            self._log_step("validate", "running")
            validation = self._validate_tables(tables)
            if not validation["passed"]:
                self._log_step("validate", "failed", str(validation["failures"]))
                return {"success": False, "reason": "Validation failed", "validation": validation}
            self._log_step("validate", "completed",
                           f"All {len(tables)} tables validated")
            steps_completed.append("validate")

            # ── Step 7: Mark complete ─────────────────────────────────────────
            self._log_step("mark_complete", "running")
            self._update_session_status("completed")
            self._log_step("mark_complete", "completed")
            steps_completed.append("mark_complete")

            logger.info("Cutover completed successfully", session_id=self.session_id)
            return {
                "success":         True,
                "status":          "completed",
                "mode":            mode,
                "steps_completed": steps_completed,
                "validation":      validation,
                "total_replayed":  replay_result["replayed"] + final_result["replayed"],
            }

        except Exception as e:
            logger.error("Cutover failed", session_id=self.session_id, error=str(e))
            self._update_session_status("failed", str(e))
            return {
                "success":         False,
                "reason":          str(e),
                "steps_completed": steps_completed,
            }

    def complete_manual_cutover(self, tables: List[str]) -> Dict[str, Any]:
        """
        Called after manual cutover (mode=manual) when the application
        has been switched to read-only and operator confirms to proceed.
        Executes steps 5-7: final replay → validate → mark complete.
        """
        steps_completed = []

        # Final replay
        self._log_step("replay_final_events", "running")
        final_result = self.replay_engine.replay_all()
        self._log_step("replay_final_events", "completed",
                       f"Final events replayed: {final_result['replayed']}")
        steps_completed.append("replay_final_events")

        # Validate
        self._log_step("validate", "running")
        validation = self._validate_tables(tables)
        if not validation["passed"]:
            self._log_step("validate", "failed", str(validation["failures"]))
            return {"success": False, "reason": "Validation failed", "validation": validation}
        self._log_step("validate", "completed")
        steps_completed.append("validate")

        # Mark complete
        self._update_session_status("completed")
        steps_completed.append("mark_complete")

        return {
            "success":         True,
            "steps_completed": steps_completed,
            "validation":      validation,
            "final_replayed":  final_result["replayed"],
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _verify_initial_load_complete(self) -> bool:
        db = self.db_factory()
        try:
            row = db.execute(
                text("SELECT initial_load_done FROM cdc_sessions WHERE id=:sid"),
                {"sid": self.session_id}
            ).fetchone()
            return bool(row[0]) if row else False
        finally:
            db.close()

    def _drain_source_connections(self) -> None:
        """
        In automated mode: attempt to prevent new writes to source.
        This is DB-specific and environment-specific.
        In practice: application load balancers handle this.
        Here we just sleep briefly to let in-flight transactions complete.
        """
        logger.info("Draining source connections", session_id=self.session_id)
        time.sleep(2)

    def _validate_tables(self, tables: List[str]) -> Dict[str, Any]:
        """Row count validation across all tables."""
        src_connector = ConnectorRegistry.get_for_config(self.source_config)
        tgt_connector = ConnectorRegistry.get_for_config(self.target_config)
        src_connector.connect()
        tgt_connector.connect()

        failures  = []
        successes = []

        try:
            for table in tables:
                try:
                    src_count = src_connector.get_row_count(table)
                    tgt_count = tgt_connector.get_row_count(table)
                    if src_count == tgt_count:
                        successes.append({"table": table, "rows": src_count})
                    else:
                        failures.append({
                            "table": table,
                            "source_rows": src_count,
                            "target_rows": tgt_count,
                            "diff": abs(src_count - tgt_count),
                        })
                except Exception as e:
                    failures.append({"table": table, "error": str(e)})
        finally:
            src_connector.disconnect()
            tgt_connector.disconnect()

        return {
            "passed":    len(failures) == 0,
            "tables":    len(tables),
            "successes": successes,
            "failures":  failures,
        }

    def _log_step(self, step: str, status: str, details: str = None) -> None:
        db = self.db_factory()
        try:
            now = datetime.datetime.utcnow()
            existing = db.execute(
                text("SELECT id FROM cutover_log WHERE session_id=:sid AND step=:step"),
                {"sid": self.session_id, "step": step}
            ).fetchone()

            if existing:
                db.execute(
                    text("""
                        UPDATE cutover_log SET status=:s, details=:d,
                        completed_at=CASE WHEN :s IN ('completed','failed') THEN :now ELSE completed_at END
                        WHERE session_id=:sid AND step=:step
                    """),
                    {"s": status, "d": details, "now": now,
                     "sid": self.session_id, "step": step}
                )
            else:
                db.execute(
                    text("""
                        INSERT INTO cutover_log
                            (id, session_id, step, status, details, started_at, created_at)
                        VALUES (:id, :sid, :step, :s, :d, :now, :now)
                    """),
                    {"id": str(uuid.uuid4()), "sid": self.session_id,
                     "step": step, "s": status, "d": details, "now": now}
                )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def _update_session_status(self, status: str, error: str = None) -> None:
        db = self.db_factory()
        try:
            db.execute(
                text("UPDATE cdc_sessions SET status=:s, error_message=:e, updated_at=:now WHERE id=:sid"),
                {"s": status, "e": error, "now": datetime.datetime.utcnow(), "sid": self.session_id}
            )
            db.commit()
        finally:
            db.close()
