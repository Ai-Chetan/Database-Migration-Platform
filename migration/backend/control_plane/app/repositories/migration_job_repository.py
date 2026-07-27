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

    def list_jobs(
        self, db: Session, tenant_id: Optional[str] = None,
        status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[MigrationJob]:
        q = db.query(MigrationJob)
        if tenant_id:
            q = q.filter(MigrationJob.tenant_id == tenant_id)
        if status:
            q = q.filter(MigrationJob.status == status)
        return q.order_by(MigrationJob.created_at.desc()).offset(offset).limit(limit).all()

    def update_job_status(self, db: Session, job_id: str, status: str) -> Optional[MigrationJob]:
        job = self.get_job_by_id(db, job_id)
        if job:
            job.status = status
            db.commit()
            db.refresh(job)
        return job

    def get_active_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.status.in_(["running", "queued", "planning"])).all()

    def get_failed_jobs(self, db: Session) -> List[MigrationJob]:
        return db.query(MigrationJob).filter(MigrationJob.status == "failed").all()

    def delete_job(self, db: Session, job_id: str):
        job = self.get_job_by_id(db, job_id)
        if job:
            db.delete(job)
            db.commit()
