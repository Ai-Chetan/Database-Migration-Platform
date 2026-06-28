"""
Rollback Engine
File: migration/backend/enterprise/rollback_engine/rollback_engine.py

Every migration plan auto-generates a rollback plan.
If migration fails at step 7 of 20, this engine cleanly reverses
all work done so far and restores the target DB to its pre-migration state.

Rollback strategies per table:
  - TRUNCATE:           fast, removes all rows (used when target table was empty)
  - DROP:               removes the table entirely (used when table was created fresh)
  - DELETE_RANGE:       removes rows by PK range (used for partial loads)
  - RESTORE_BACKUP:     uses a pre-migration snapshot (future)
  - NOOP:               table had pre-existing data, don't touch it

Rollback execution order is REVERSE of migration order:
  Migration: country → state → city → customer → orders
  Rollback:  orders → customer → city → state → country
  (FK constraints require this reverse order)

Usage:
    engine = RollbackEngine()

    # Generate rollback plan before migration starts
    plan = engine.generate_plan(db, job_id, migration_plan, target_config)

    # Execute rollback when migration fails
    engine.execute(db, plan_id, target_config)
"""

import uuid
import datetime
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


@dataclass
class RollbackStep:
    step_number:  int
    step_type:    str          # truncate | drop | delete_range | restore_fks | noop
    table_name:   str
    pk_column:    str = "id"
    pk_min:       Optional[int] = None
    pk_max:       Optional[int] = None
    restore_constraints_sql: List[str] = field(default_factory=list)
    notes:        str = ""


class RollbackEngine:

    def generate_plan(
        self,
        db:              Session,
        job_id:          str,
        migration_plan:  Dict[str, Any],   # from MigrationPlanGenerator
        target_config:   dict,
        table_states:    Dict[str, str] = None,  # {table: "empty"|"had_data"}
    ) -> str:
        """
        Generate a rollback plan for a migration job.
        Returns the rollback_plan_id.

        table_states: whether each target table was empty before migration started.
        If empty → DROP or TRUNCATE is safe.
        If had_data → DELETE_RANGE only (to preserve pre-existing rows).
        """
        table_states = table_states or {}
        steps = self._build_rollback_steps(migration_plan, table_states)
        plan_id = str(uuid.uuid4())

        db.execute(
            text("""
                INSERT INTO rollback_plans
                    (id, job_id, status, rollback_steps,
                     tables_affected, created_at)
                VALUES
                    (:id, :jid, 'ready', :steps::jsonb,
                     :tables::jsonb, :now)
            """),
            {
                "id":     plan_id,
                "jid":    job_id,
                "steps":  json.dumps([self._step_to_dict(s) for s in steps]),
                "tables": json.dumps(list({s.table_name for s in steps})),
                "now":    datetime.datetime.utcnow(),
            }
        )

        # Link plan to job
        db.execute(
            text("UPDATE migration_jobs SET rollback_plan_id = :pid WHERE id = :jid"),
            {"pid": plan_id, "jid": job_id}
        )
        db.commit()

        logger.info(
            "Rollback plan generated",
            job_id=job_id,
            plan_id=plan_id,
            steps=len(steps)
        )
        return plan_id

    def execute(
        self,
        db:            Session,
        plan_id:       str,
        target_config: dict,
        dry_run:       bool = False,
    ) -> Dict[str, Any]:
        """
        Execute the rollback plan.
        dry_run=True prints what would happen without executing.

        Returns summary dict with steps executed, rows removed, errors.
        """
        # Fetch plan
        row = db.execute(
            text("SELECT * FROM rollback_plans WHERE id = :id"),
            {"id": plan_id}
        ).fetchone()

        if not row:
            raise ValueError(f"Rollback plan {plan_id} not found")

        plan_dict = dict(row._mapping)
        steps_raw = plan_dict.get("rollback_steps", [])
        if isinstance(steps_raw, str):
            steps_raw = json.loads(steps_raw)

        if not dry_run:
            db.execute(
                text("""
                    UPDATE rollback_plans
                    SET status='executing', executed_at=:now
                    WHERE id=:id
                """),
                {"now": datetime.datetime.utcnow(), "id": plan_id}
            )
            db.commit()

        results = []
        total_rows_removed = 0
        errors = []

        for step_dict in steps_raw:
            step_num  = step_dict["step_number"]
            step_type = step_dict["step_type"]
            tname     = step_dict["table_name"]

            if dry_run:
                results.append({
                    "step": step_num, "table": tname, "type": step_type,
                    "dry_run": True, "would_execute": self._describe_step(step_dict)
                })
                continue

            result = self._execute_step(db, plan_id, step_dict, target_config)
            results.append(result)
            total_rows_removed += result.get("rows_affected", 0)
            if result.get("error"):
                errors.append(f"Step {step_num} ({tname}): {result['error']}")

        if not dry_run:
            final_status = "completed" if not errors else "failed"
            db.execute(
                text("""
                    UPDATE rollback_plans
                    SET status=:s, completed_at=:now,
                        error_message=:err
                    WHERE id=:id
                """),
                {
                    "s":   final_status,
                    "now": datetime.datetime.utcnow(),
                    "err": "; ".join(errors) if errors else None,
                    "id":  plan_id,
                }
            )
            db.commit()

        return {
            "plan_id":           plan_id,
            "dry_run":           dry_run,
            "steps_executed":    len(results),
            "total_rows_removed": total_rows_removed,
            "errors":            errors,
            "success":           len(errors) == 0,
            "steps":             results,
        }

    def get_plan(self, db: Session, plan_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM rollback_plans WHERE id = :id"),
            {"id": plan_id}
        ).fetchone()
        if not row:
            return None
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):      d[k] = str(v)
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
        if isinstance(d.get("rollback_steps"), str):
            d["rollback_steps"] = json.loads(d["rollback_steps"])
        if isinstance(d.get("tables_affected"), str):
            d["tables_affected"] = json.loads(d["tables_affected"])
        return d

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_rollback_steps(
        self,
        migration_plan: Dict,
        table_states:   Dict[str, str],
    ) -> List[RollbackStep]:
        """
        Build rollback steps in REVERSE order of migration.
        FK constraints require reverse order (child tables before parent).
        """
        steps = []

        # Get all auto tables from plan in original order
        auto_tables   = migration_plan.get("auto_tables", [])
        manual_tables = migration_plan.get("manual_tables", [])
        all_tables    = list(reversed(auto_tables + manual_tables))

        step_num = 1

        # Step 1: Remove FK constraints first (so truncate/drop can work)
        steps.append(RollbackStep(
            step_number=step_num,
            step_type="disable_fks",
            table_name="__all__",
            notes="Disable FK constraint checks before rollback"
        ))
        step_num += 1

        # Steps: roll back each table in reverse order
        for tname in all_tables:
            state = table_states.get(tname, "empty")

            if state == "had_data":
                # Don't drop/truncate — only delete rows we added (by chunk ranges)
                step_type = "delete_migrated_rows"
            else:
                # Table was empty before migration — truncate is safe and fast
                step_type = "truncate"

            steps.append(RollbackStep(
                step_number=step_num,
                step_type=step_type,
                table_name=tname,
                notes=f"Rollback {tname}: {step_type}"
            ))
            step_num += 1

        # Final step: re-enable FK constraints
        steps.append(RollbackStep(
            step_number=step_num,
            step_type="enable_fks",
            table_name="__all__",
            notes="Re-enable FK constraint checks after rollback"
        ))

        return steps

    def _execute_step(
        self,
        db:            Session,
        plan_id:       str,
        step_dict:     dict,
        target_config: dict,
    ) -> dict:
        step_num  = step_dict["step_number"]
        step_type = step_dict["step_type"]
        tname     = step_dict["table_name"]
        log_id    = str(uuid.uuid4())
        now       = datetime.datetime.utcnow()

        # Log step start
        db.execute(
            text("""
                INSERT INTO rollback_execution_log
                    (id, rollback_plan_id, step_number, step_type,
                     table_name, status, started_at, created_at)
                VALUES (:id, :pid, :snum, :stype, :tname, 'running', :now, :now)
            """),
            {"id": log_id, "pid": plan_id, "snum": step_num,
             "stype": step_type, "tname": tname, "now": now}
        )
        db.commit()

        try:
            conn    = self._connect(target_config)
            cursor  = conn.cursor()
            engine  = target_config.get("engine", "mysql").lower()
            sql     = None
            rows    = 0

            if step_type == "disable_fks":
                sql = "SET FOREIGN_KEY_CHECKS = 0" if engine == "mysql" else "SET session_replication_role = replica"
                cursor.execute(sql)

            elif step_type == "enable_fks":
                sql = "SET FOREIGN_KEY_CHECKS = 1" if engine == "mysql" else "SET session_replication_role = DEFAULT"
                cursor.execute(sql)

            elif step_type == "truncate":
                sql = f"TRUNCATE TABLE `{tname}`" if engine == "mysql" else f'TRUNCATE TABLE "{tname}"'
                cursor.execute(sql)
                rows = cursor.rowcount if cursor.rowcount >= 0 else 0

            elif step_type == "drop":
                sql = f"DROP TABLE IF EXISTS `{tname}`" if engine == "mysql" else f'DROP TABLE IF EXISTS "{tname}"'
                cursor.execute(sql)

            elif step_type == "delete_migrated_rows":
                # Delete all rows in target that match rows we inserted
                # We use chunk metadata to identify ranges
                migrated_ranges = self._get_migrated_ranges(db, tname)
                pk_col = step_dict.get("pk_column", "id")
                total_deleted = 0
                for pk_start, pk_end in migrated_ranges:
                    if engine == "mysql":
                        del_sql = f"DELETE FROM `{tname}` WHERE `{pk_col}` BETWEEN {pk_start} AND {pk_end}"
                    else:
                        del_sql = f'DELETE FROM "{tname}" WHERE "{pk_col}" BETWEEN {pk_start} AND {pk_end}'
                    cursor.execute(del_sql)
                    total_deleted += cursor.rowcount if cursor.rowcount >= 0 else 0
                    sql = del_sql
                rows = total_deleted

            conn.commit()
            cursor.close()
            conn.close()

            # Update log
            db.execute(
                text("""
                    UPDATE rollback_execution_log
                    SET status='completed', sql_executed=:sql,
                        rows_affected=:rows, completed_at=:now
                    WHERE id=:id
                """),
                {"sql": sql, "rows": rows, "now": datetime.datetime.utcnow(), "id": log_id}
            )
            db.commit()

            logger.info("Rollback step completed", step=step_num, table=tname, type=step_type, rows=rows)
            return {"step": step_num, "table": tname, "type": step_type, "rows_affected": rows, "success": True}

        except Exception as e:
            logger.error("Rollback step failed", step=step_num, table=tname, error=str(e))
            db.execute(
                text("""
                    UPDATE rollback_execution_log
                    SET status='failed', error_message=:err, completed_at=:now
                    WHERE id=:id
                """),
                {"err": str(e), "now": datetime.datetime.utcnow(), "id": log_id}
            )
            db.commit()
            return {"step": step_num, "table": tname, "type": step_type, "rows_affected": 0,
                    "success": False, "error": str(e)}

    def _get_migrated_ranges(self, db: Session, table_name: str) -> List[tuple]:
        """Get all successfully migrated PK ranges for a table."""
        rows = db.execute(
            text("""
                SELECT mc.pk_start, mc.pk_end
                FROM migration_chunks mc
                JOIN migration_tables mt ON mc.table_id = mt.id
                WHERE mt.table_name = :tname AND mc.status = 'completed'
                ORDER BY mc.pk_start
            """),
            {"tname": table_name}
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def _describe_step(self, step_dict: dict) -> str:
        t = step_dict.get("step_type")
        n = step_dict.get("table_name")
        if t == "disable_fks":  return "SET FOREIGN_KEY_CHECKS = 0"
        if t == "enable_fks":   return "SET FOREIGN_KEY_CHECKS = 1"
        if t == "truncate":     return f"TRUNCATE TABLE `{n}`"
        if t == "drop":         return f"DROP TABLE IF EXISTS `{n}`"
        if t == "delete_migrated_rows": return f"DELETE FROM `{n}` WHERE pk IN migrated ranges"
        return f"{t} on {n}"

    def _step_to_dict(self, step: RollbackStep) -> dict:
        return {
            "step_number": step.step_number,
            "step_type":   step.step_type,
            "table_name":  step.table_name,
            "pk_column":   step.pk_column,
            "pk_min":      step.pk_min,
            "pk_max":      step.pk_max,
            "notes":       step.notes,
        }

    def _connect(self, config: dict):
        engine = config.get("engine", "mysql").lower()
        if engine in ("mysql", "mariadb"):
            import mysql.connector
            return mysql.connector.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                autocommit=False,
                connection_timeout=30,
            )
        else:
            import psycopg2
            conn = psycopg2.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 5432)),
                dbname=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                connect_timeout=30,
            )
            conn.autocommit = False
            return conn
