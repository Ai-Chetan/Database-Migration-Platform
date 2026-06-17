from typing import List, Optional
from sqlalchemy.orm import Session
from backend.control_plane.app.models.migration import MigrationJob
import uuid

class MigrationJobRepository:
    def create_job(self, db: Session, config: dict = None) -> MigrationJob:
        job = MigrationJob(id=str(uuid.uuid4()), config=config, status="PENDING")
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
