from sqlalchemy.orm import Session
from backend.control_plane.app.repositories.migration_job_repository import MigrationJobRepository
from backend.shared.constants.statuses import MigrationJobStatus
from backend.shared.exceptions.base import PlatformException
from backend.shared.config.logging import logger

class JobManager:
    def __init__(self):
        self.job_repo = MigrationJobRepository()

    def create_job(self, db: Session, config: dict = None) -> str:
        logger.info("Creating migration job")
        job = self.job_repo.create_job(db, config)
        return job.id

    def start_job(self, db: Session, job_id: str):
        logger.info("Starting migration job", job_id=job_id)
        job = self.job_repo.get_job_by_id(db, job_id)
        if not job:
            raise PlatformException(code="JOB_NOT_FOUND", message="Job not found")
        self.job_repo.update_job_status(db, job_id, MigrationJobStatus.PLANNING)

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
