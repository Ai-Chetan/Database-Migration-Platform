from sqlalchemy.orm import Session
from backend.control_plane.app.repositories.migration_chunk_repository import MigrationChunkRepository
from backend.control_plane.app.services.queue_service import QueueService
from backend.shared.config.logging import logger
from backend.shared.utils.chunking import generate_pk_chunks

class Planner:
    def __init__(self):
        self.chunk_repo = MigrationChunkRepository()
        self.queue_service = QueueService()

    def generate_chunks(self, db: Session, job_id: str, table_id: str, table_name: str, total_rows: int, chunk_size: int = 100000):
        logger.info("Generating chunks", job_id=job_id, table_id=table_id, total_rows=total_rows)
        # Using a simple 1 to total_rows assumption for MVP PK range
        chunks = generate_pk_chunks(1, total_rows, chunk_size)

        chunks_data = []
        for min_pk, max_pk in chunks:
            chunks_data.append({
                "job_id": job_id,
                "table_id": table_id,
                "pk_start": min_pk,
                "pk_end": max_pk,
                "status": "pending",
                # NOTE: no "table_name" key here - migration_chunks has no such
                # column (table_name lives on migration_tables, joined via
                # table_id). Passing it through **data into the MigrationChunk
                # constructor would raise "unexpected keyword argument" the
                # first time this ran - this method had never actually been
                # called from anywhere until this fix wired it up.
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
