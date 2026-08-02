from sqlalchemy.orm import Session
from sqlalchemy import text
import datetime
from backend.control_plane.app.repositories.migration_job_repository import MigrationJobRepository
from backend.shared.constants.statuses import MigrationJobStatus
from backend.shared.exceptions.base import PlatformException
from backend.shared.config.logging import logger


class JobManager:
    def __init__(self):
        self.job_repo = MigrationJobRepository()

    def create_job(
        self,
        db,
        source_config,
        target_config,
        tenant_id="local"
    ):

        return self.job_repo.create_job(
            db=db,
            source_config=source_config,
            target_config=target_config,
            tenant_id=tenant_id
        )


    def start_job(self, db: Session, job_id: str):
        """
        Starts the job. Chunk generation already happened during
        POST /planning/compute (see control_plane/app/orchestrator/planner.py),
        so there's no real "planning" computation left to do here - this
        transitions straight to 'running'.

        Previously this set status to PLANNING, and nothing anywhere else
        in the codebase ever transitioned a job to 'running' (workflow_executor
        only ever writes 'completed'/'failed' when chunks finish) - so a job
        would sit labeled "Planning" for its entire execution, and the
        frontend's isRunning checks (live-pulse indicators, throughput
        sparklines) would never activate.
        """
        logger.info("Starting migration job", job_id=job_id)
        job = self.job_repo.get_job_by_id(db, job_id)
        if not job:
            raise PlatformException(code="JOB_NOT_FOUND", message="Job not found")
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.RUNNING)
        if not job.started_at:
            db.execute(
                text("UPDATE migration_jobs SET started_at=:now WHERE id=:id AND started_at IS NULL"),
                {"now": datetime.datetime.utcnow(), "id": job_id}
            )
            db.commit()

    def pause_job(self, db: Session, job_id: str):
        logger.info("Pausing migration job", job_id=job_id)
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.PAUSED)

    def resume_job(self, db: Session, job_id: str):
        logger.info("Resuming migration job", job_id=job_id)
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.RUNNING)

    def cancel_job(self, db: Session, job_id: str):
        logger.info("Canceling migration job", job_id=job_id)
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.CANCELLED)

    def complete_job(self, db: Session, job_id: str):
        logger.info("Completing migration job", job_id=job_id)
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.COMPLETED)

    def fail_job(self, db: Session, job_id: str):
        logger.warning("Failing migration job", job_id=job_id)
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.FAILED)
