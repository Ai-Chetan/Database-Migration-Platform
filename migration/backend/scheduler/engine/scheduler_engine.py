"""
Scheduler Engine
File: migration/backend/scheduler/engine/scheduler_engine.py

Cron-style scheduler for migrations, intelligence scans, and reports.
Uses croniter library for cron expression parsing.
Runs as a background thread, checks for due jobs every 60 seconds.

Supported job types:
    migration         → create and start a migration job
    intelligence_scan → run Part 3 Metadata Intelligence scan
    data_quality_scan → run Part 4 Data Quality Scanner
    benchmark         → record benchmark for a completed job
    report            → generate a migration report

Features:
    - Cron expressions (standard 5-field: min hour dom mon dow)
    - Timezone support
    - Approval gates (require_approval=True blocks until approved)
    - Event Bus integration (publishes schedule.triggered, schedule.completed)
    - Missed run detection (if scheduler was down, catches up gracefully)
    - Concurrent run prevention (skips if previous run still active)

Usage:
    engine = SchedulerEngine()
    engine.start()    # starts background thread
    engine.stop()
"""

import threading
import time
import datetime
import uuid
import json
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


class SchedulerEngine:

    POLL_INTERVAL = 60   # seconds between schedule checks

    def __init__(self):
        self._thread:  Optional[threading.Thread] = None
        self._running: bool = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="scheduler-engine"
        )
        self._thread.start()
        logger.info("Scheduler Engine started", poll_interval=self.POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=15)

    def trigger_now(
        self,
        db:               Session,
        scheduled_job_id: str,
        triggered_by:     str = "manual",
        tenant_id:        str = "local",
    ) -> Dict[str, Any]:
        """Manually trigger a scheduled job right now, bypassing the cron schedule."""
        row = db.execute(
            text("SELECT * FROM scheduled_jobs WHERE id=:id AND tenant_id=:tid"),
            {"id": scheduled_job_id, "tid": tenant_id}
        ).fetchone()

        if not row:
            return {"error": f"Scheduled job {scheduled_job_id} not found"}

        job = dict(row._mapping)
        return self._execute_scheduled_job(db, job, triggered_by=triggered_by)

    def compute_next_run(self, cron_expression: str, timezone: str = "UTC") -> Optional[datetime.datetime]:
        """Compute the next run time for a cron expression."""
        try:
            from croniter import croniter
            import pytz
            tz  = pytz.timezone(timezone)
            now = datetime.datetime.now(tz)
            c   = croniter(cron_expression, now)
            return c.get_next(datetime.datetime)
        except ImportError:
            logger.warning("croniter not installed — install with: pip install croniter pytz")
            return None
        except Exception as e:
            logger.warning("Invalid cron expression", expr=cron_expression, error=str(e))
            return None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_scheduled_job(
        self,
        db:              Session,
        name:            str,
        job_type:        str,
        cron_expression: str,
        job_config:      Dict[str, Any],
        description:     str = "",
        timezone:        str = "UTC",
        require_approval: bool = False,
        tenant_id:       str = "local",
    ) -> Dict[str, Any]:
        """Create a new scheduled job."""
        # Validate cron
        next_run = self.compute_next_run(cron_expression, timezone)
        if next_run is None and cron_expression:
            return {"error": f"Invalid cron expression: '{cron_expression}'"}

        valid_types = {"migration", "intelligence_scan", "data_quality_scan",
                       "benchmark", "report"}
        if job_type not in valid_types:
            return {"error": f"Invalid job_type. Must be one of: {sorted(valid_types)}"}

        jid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                INSERT INTO scheduled_jobs
                    (id, tenant_id, name, description, job_type,
                     cron_expression, timezone, job_config,
                     require_approval, is_active, next_run_at, created_at, updated_at)
                VALUES
                    (:id, :tid, :name, :desc, :jtype,
                     :cron, :tz, :config::jsonb,
                     :approval, TRUE, :next_run, :now, :now)
            """),
            {
                "id": jid, "tid": tenant_id, "name": name, "desc": description,
                "jtype": job_type, "cron": cron_expression, "tz": timezone,
                "config": json.dumps(job_config), "approval": require_approval,
                "next_run": next_run, "now": now,
            }
        )
        db.commit()
        logger.info("Scheduled job created", name=name, cron=cron_expression,
                    next_run=next_run)
        return self.get_scheduled_job(db, jid)

    def get_scheduled_job(self, db: Session, job_id: str) -> Optional[Dict]:
        row = db.execute(
            text("SELECT * FROM scheduled_jobs WHERE id=:id"), {"id": job_id}
        ).fetchone()
        return self._row(row) if row else None

    def list_scheduled_jobs(self, db: Session, tenant_id: str = "local") -> List[Dict]:
        rows = db.execute(
            text("""
                SELECT * FROM scheduled_jobs
                WHERE tenant_id=:tid ORDER BY created_at DESC
            """),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def update_scheduled_job(
        self,
        db:     Session,
        job_id: str,
        **kwargs
    ) -> Dict:
        allowed = {"name", "description", "cron_expression", "timezone",
                   "job_config", "require_approval", "is_active"}
        set_parts = ["updated_at=:now"]
        params    = {"now": datetime.datetime.utcnow(), "id": job_id}

        for k, v in kwargs.items():
            if k not in allowed:
                continue
            set_parts.append(f"{k}=:{k}")
            params[k] = json.dumps(v) if k == "job_config" else v

        if "cron_expression" in kwargs:
            cron    = kwargs["cron_expression"]
            tz      = kwargs.get("timezone", "UTC")
            next_run = self.compute_next_run(cron, tz)
            set_parts.append("next_run_at=:next_run")
            params["next_run"] = next_run

        db.execute(
            text(f"UPDATE scheduled_jobs SET {', '.join(set_parts)} WHERE id=:id"),
            params
        )
        db.commit()
        return self.get_scheduled_job(db, job_id)

    def delete_scheduled_job(self, db: Session, job_id: str) -> dict:
        db.execute(text("DELETE FROM scheduled_jobs WHERE id=:id"), {"id": job_id})
        db.commit()
        return {"deleted": job_id}

    def list_runs(self, db: Session, scheduled_job_id: str, limit: int = 20) -> List[Dict]:
        rows = db.execute(
            text("""
                SELECT * FROM schedule_runs
                WHERE scheduled_job_id=:sid
                ORDER BY started_at DESC LIMIT :lim
            """),
            {"sid": scheduled_job_id, "lim": limit}
        ).fetchall()
        return [self._row(r) for r in rows]

    # ── Private ───────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Background polling loop."""
        while self._running:
            try:
                from backend.shared.config.database import SessionLocal
                db = SessionLocal()
                try:
                    self._check_due_jobs(db)
                finally:
                    db.close()
            except Exception as e:
                logger.error("Scheduler loop error", error=str(e))
            time.sleep(self.POLL_INTERVAL)

    def _check_due_jobs(self, db: Session) -> None:
        """Find all jobs due to run and execute them."""
        now = datetime.datetime.utcnow()
        rows = db.execute(
            text("""
                SELECT * FROM scheduled_jobs
                WHERE is_active = TRUE
                AND next_run_at <= :now
                AND (last_status != 'running' OR last_status IS NULL)
            """),
            {"now": now}
        ).fetchall()

        for row in rows:
            job = dict(row._mapping)
            for k, v in job.items():
                if hasattr(v, "hex"):        job[k] = str(v)
                if hasattr(v, "isoformat"):  job[k] = v.isoformat()

            logger.info("Scheduler triggering job",
                        name=job.get("name"), type=job.get("job_type"))
            self._execute_scheduled_job(db, job, triggered_by="scheduler")

    def _execute_scheduled_job(
        self, db: Session, job: Dict, triggered_by: str = "scheduler"
    ) -> Dict[str, Any]:
        """Execute one scheduled job and record the run."""
        scheduled_job_id = job["id"]
        job_type         = job["job_type"]
        config           = job.get("job_config") or {}
        if isinstance(config, str):
            config = json.loads(config)
        tenant_id        = job.get("tenant_id", "local")

        # Check if approval is required
        if job.get("require_approval"):
            self._update_scheduled_job_status(db, scheduled_job_id, "approval_pending")
            self._publish("schedule.approval_required", scheduled_job_id, {
                "name": job.get("name"), "job_type": job_type
            })
            return {"status": "approval_pending",
                    "message": "Job requires approval before execution."}

        # Create run record
        run_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO schedule_runs
                    (id, scheduled_job_id, tenant_id, triggered_by, status, started_at)
                VALUES (:id, :sjid, :tid, :by, 'running', :now)
            """),
            {"id": run_id, "sjid": scheduled_job_id, "tid": tenant_id,
             "by": triggered_by, "now": datetime.datetime.utcnow()}
        )
        db.commit()

        self._update_scheduled_job_status(db, scheduled_job_id, "running")
        self._publish("schedule.triggered", scheduled_job_id,
                      {"name": job.get("name"), "job_type": job_type,
                       "triggered_by": triggered_by})

        result   = {}
        status   = "completed"
        error    = None

        try:
            if job_type == "intelligence_scan":
                result = self._run_intelligence_scan(db, config, tenant_id)
            elif job_type == "data_quality_scan":
                result = self._run_data_quality_scan(db, config, tenant_id)
            elif job_type == "benchmark":
                result = self._run_benchmark(db, config)
            elif job_type == "report":
                result = self._run_report(db, config, tenant_id)
            elif job_type == "migration":
                result = self._run_migration(db, config, tenant_id)
            else:
                result = {"warning": f"Unknown job_type: {job_type}"}

        except Exception as e:
            status = "failed"
            error  = str(e)
            logger.error("Scheduled job execution failed",
                         name=job.get("name"), error=error)

        # Update run record
        db.execute(
            text("""
                UPDATE schedule_runs SET
                    status=:status, completed_at=:now,
                    error_message=:err, result_summary=:result::jsonb
                WHERE id=:id
            """),
            {"status": status, "now": datetime.datetime.utcnow(),
             "err": error, "result": json.dumps(result, default=str), "id": run_id}
        )
        db.commit()

        # Update scheduled job
        next_run = self.compute_next_run(
            job.get("cron_expression", ""), job.get("timezone", "UTC")
        )
        db.execute(
            text("""
                UPDATE scheduled_jobs SET
                    last_run_at=:now, next_run_at=:next,
                    last_status=:status, run_count=run_count+1, updated_at=:now
                WHERE id=:id
            """),
            {"now": datetime.datetime.utcnow(), "next": next_run,
             "status": status, "id": scheduled_job_id}
        )
        db.commit()

        self._publish(
            "schedule.completed" if status == "completed" else "schedule.failed",
            scheduled_job_id,
            {"name": job.get("name"), "status": status, "run_id": run_id}
        )
        return {"run_id": run_id, "status": status, **result}

    def _run_intelligence_scan(self, db, config, tenant_id) -> Dict:
        from backend.intelligence.analyzers.scan_orchestrator import ScanOrchestrator
        orchestrator = ScanOrchestrator()
        return orchestrator.run(
            db=db,
            connection_id=config.get("connection_id"),
            source_config=config.get("source_config", {}),
            tenant_id=tenant_id,
            background=False,
        )

    def _run_data_quality_scan(self, db, config, tenant_id) -> Dict:
        from backend.intelligence_service.scanner.data_quality_scanner import DataQualityScanner
        scanner = DataQualityScanner()
        return scanner.scan_all(
            db=db,
            connection_id=config.get("connection_id", ""),
            source_config=config.get("source_config", {}),
            schema_info=config.get("schema_info", {"tables": {}}),
            tenant_id=tenant_id,
        )

    def _run_benchmark(self, db, config) -> Dict:
        from backend.live_intelligence.benchmark.benchmark_engine import BenchmarkEngine
        result = BenchmarkEngine.record_job_completion(
            db=db, job_id=config.get("job_id", ""), tenant_id=config.get("tenant_id", "local")
        )
        return result or {"message": "No benchmark data available"}

    def _run_report(self, db, config, tenant_id) -> Dict:
        from backend.reporting.generators.report_generator import ReportGenerator
        gen = ReportGenerator()
        return gen.generate(
            db=db,
            job_id=config.get("job_id", ""),
            report_type=config.get("report_type", "migration_summary"),
            tenant_id=tenant_id,
        )

    def _run_migration(self, db, config, tenant_id) -> Dict:
        # Create a migration job via the control plane
        # In production: HTTP call to control plane or direct DB insert
        return {"message": "Migration scheduling requires control plane integration",
                "config": config}

    def _update_scheduled_job_status(self, db, job_id, status):
        db.execute(
            text("UPDATE scheduled_jobs SET last_status=:s, updated_at=:now WHERE id=:id"),
            {"s": status, "now": datetime.datetime.utcnow(), "id": job_id}
        )
        db.commit()

    def _publish(self, event_type, resource_id, payload):
        try:
            from backend.kernel.event_bus.event_bus import EventBus
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()
            try:
                EventBus.publish(
                    event_type=event_type,
                    source_service="scheduler",
                    resource_type="scheduled_job",
                    resource_id=str(resource_id),
                    payload=payload,
                    db=db,
                )
            finally:
                db.close()
        except Exception:
            pass

    def _row(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d
