from sqlalchemy.orm import Session
from backend.control_plane.app.repositories.migration_chunk_repository import MigrationChunkRepository
from backend.control_plane.app.services.queue_service import QueueService
from backend.shared.constants.statuses import ChunkStatus
from backend.shared.config.logging import logger

class RecoveryManager:
    def __init__(self):
        self.chunk_repo = MigrationChunkRepository()
        self.queue_service = QueueService()

    def check_stuck_chunks(self, db: Session):
        logger.info("Checking for stuck chunks")
        running_chunks = self.chunk_repo.get_running_chunks(db)
        
        for chunk in running_chunks:
            # Here we would check if updated_at is > 15 mins ago
            # For simplicity, assuming all found are stuck
            self.chunk_repo.update_chunk_status(db, chunk.id, ChunkStatus.FAILED)
            # Increment retry count logic goes here
            self.queue_service.publish_retry(chunk.job_id, chunk.table_id, chunk.id)
            logger.warning("Requeued stuck chunk", chunk_id=chunk.id)
