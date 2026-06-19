from sqlalchemy.orm import Session
from backend.shared.config.database import get_db, engine
from backend.control_plane.app.models.migration import Base
from backend.control_plane.app.orchestrator.job_manager import JobManager
from backend.control_plane.app.orchestrator.planner import Planner
from backend.control_plane.app.repositories.migration_table_repository import MigrationTableRepository
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
import json

def test_milestone():
    # 1. Setup DB
    Base.metadata.create_all(bind=engine)
    
    db: Session = next(get_db())
    
    # 2. Create Job
    job_manager = JobManager()
    job = job_manager.create_job(
        db=db,
        source_config={
            "engine": "mysql"
        },
        target_config={
            "engine": "postgres"
        }
    )

    job_id = job.id
    print(f"Created Job: {job.id}")
    
    # 3. Create Table Entry
    table_repo = MigrationTableRepository()
    table = table_repo.create_table_entry(
        db=db,
        job_id=job.id,
        table_name="users",
        primary_key_column="id"
    )
    print(f"Created Table Entry: {table.id}")
    
    # 4. Generate Chunks & publish to Redis
    planner = Planner()
    chunks = planner.generate_chunks(db, job_id, table.id, table.table_name, 500000, 100000)
    print(f"Generated {len(chunks)} chunks.")
    
    # 5. Verify Redis
    queue_len = redis_client.llen(Queues.MIGRATION_QUEUE)
    print(f"Items in Redis {Queues.MIGRATION_QUEUE}: {queue_len}")
    
    item = redis_client.rpop(Queues.MIGRATION_QUEUE)
    if item:
        print(f"Sample queued item: {json.loads(item)}")
        
if __name__ == "__main__":
    test_milestone()
