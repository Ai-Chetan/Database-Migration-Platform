from typing import List, Optional
from sqlalchemy.orm import Session
from backend.control_plane.app.models.migration import MigrationJob
import uuid

class MigrationJobRepository:

    def create_job(
        self,
        db,
        source_config: dict,
        target_config: dict,
        tenant_id: str = "local"
    ):

        job = MigrationJob(
            tenant_id=tenant_id,
            status="pending",
            source_config=source_config,
            target_config=target_config
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        return job

    def get_job_by_id(self, db: Session, job_id: str) -> Optional[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.id == job_id).first()

    def update_job_status(self, db: Session, job_id: str, status: str) -> Optional[MigrationJob]:
        job = self.get_job_by_id(db, job_id)
        if job:
            job.status = status
            db.commit()
            db.refresh(job)
        return job

    def get_active_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.status.in_(["RUNNING", "QUEUED", "PLANNING"])).all()

    def get_failed_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.status == "FAILED").all()

    def delete_job(self, db: Session, job_id: str):
        job = self.get_job_by_id(db, job_id)
        if job:
            db.delete(job)
            db.commit()
