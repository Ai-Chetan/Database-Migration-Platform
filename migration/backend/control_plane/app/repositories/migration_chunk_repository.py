from typing import List, Optional
from sqlalchemy.orm import Session
from backend.control_plane.app.models.migration import MigrationChunk
import uuid

class MigrationChunkRepository:
    def create_chunk(self, db: Session, job_id: str, table_id: str, table_name: str, pk_start: str, pk_end: str) -> MigrationChunk:
        chunk = MigrationChunk(
            id=uuid.uuid4(),
            job_id=job_id,
            table_id=table_id,
            table_name=table_name,
            pk_start=pk_start,
            pk_end=pk_end,
            status="pending"
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)
        return chunk

    def bulk_create_chunks(self, db: Session, chunks_data: List[dict]):
        chunks = [
            MigrationChunk(
                id=str(uuid.uuid4()),
                **data
            ) for data in chunks_data
        ]
        db.bulk_save_objects(chunks)
        db.commit()
        return chunks

    def update_chunk_status(self, db: Session, chunk_id: str, status: str) -> Optional[MigrationChunk]:
        chunk = db.query(MigrationChunk).filter(MigrationChunk.id == chunk_id).first()
        if chunk:
            chunk.status = status
            db.commit()
            db.refresh(chunk)
        return chunk

    def get_pending_chunks(self, db: Session, job_id: str) -> List[MigrationChunk]:
        return db.query(MigrationChunk).filter(MigrationChunk.job_id == job_id, MigrationChunk.status == "PENDING").all()

    def get_running_chunks(self, db: Session) -> List[MigrationChunk]:
        return db.query(MigrationChunk).filter(MigrationChunk.status == "RUNNING").all()

    def get_failed_chunks(self, db: Session) -> List[MigrationChunk]:
        return db.query(MigrationChunk).filter(MigrationChunk.status == "FAILED").all()
