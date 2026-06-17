from sqlalchemy.orm import Session
from backend.control_plane.app.repositories.migration_chunk_repository import MigrationChunkRepository
from backend.control_plane.app.services.queue_service import QueueService
from backend.shared.config.logging import logger
from backend.shared.utils.chunking import generate_pk_chunks

class Planner:
    def __init__(self):
        self.chunk_repo = MigrationChunkRepository()
        self.queue_service = QueueService()

    def generate_chunks(self, db: Session, job_id: str, table_id: str, total_rows: int, chunk_size: int = 100000):
        logger.info("Generating chunks", job_id=job_id, table_id=table_id, total_rows=total_rows)
        # Using a simple 1 to total_rows assumption for MVP PK range
        chunks = generate_pk_chunks(1, total_rows, chunk_size)
        
        chunks_data = []
        for min_pk, max_pk in chunks:
            chunks_data.append({
                "job_id": job_id,
                "table_id": table_id,
                "min_pk": str(min_pk),
                "max_pk": str(max_pk),
                "status": "PENDING"
            })
            
        created_chunks = self.chunk_repo.bulk_create_chunks(db, chunks_data)
        
        # Publish chunks
        for chunk in created_chunks:
            self.queue_service.publish_chunk(
                job_id=job_id,
                table_id=table_id,
                chunk_id=chunk.id
            )
        
        logger.info("Published chunks", count=len(created_chunks))
        return created_chunks
