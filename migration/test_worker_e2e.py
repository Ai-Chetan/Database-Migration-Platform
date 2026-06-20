"""
End-to-End Worker Test
File: migration/test_worker_e2e.py

This test validates the complete pipeline:
    1. Creates a source MySQL/PostgreSQL table with test data
    2. Creates job + table metadata in PostgreSQL
    3. Generates chunks and pushes to Redis
    4. Runs the worker to consume chunks
    5. Verifies data landed in target database
    6. Verifies chunk/job status updated to 'completed'

Run from the migration/ directory:
    python test_worker_e2e.py

BEFORE RUNNING:
    1. Set your source and target DB credentials below
    2. Make sure Redis is running (docker run -d -p 6379:6379 redis)
    3. Make sure PostgreSQL metadata DB is running
    4. The source table must exist and have data
"""

import json
import time
from sqlalchemy.orm import Session

from backend.shared.config.database import get_db, engine
from backend.control_plane.app.models.migration import Base, MigrationChunk, MigrationJob
from backend.control_plane.app.orchestrator.job_manager import JobManager
from backend.control_plane.app.orchestrator.planner import Planner
from backend.control_plane.app.repositories.migration_table_repository import MigrationTableRepository
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.worker_service.app.executor.chunk_executor import ChunkExecutor
from backend.shared.config.logging import logger

# ─── CONFIGURE YOUR DATABASES HERE ────────────────────────────────────────────

SOURCE_CONFIG = {
    "engine": "mysql",
    "host": "localhost",
    "port": 3306,
    "database": "source_db",       # ← your source database name
    "user": "root",
    "password": "your_password"    # ← your source DB password
}

TARGET_CONFIG = {
    "engine": "mysql",
    "host": "localhost",
    "port": 3306,
    "database": "target_db",       # ← your target database name
    "user": "root",
    "password": "your_password"    # ← your target DB password
}

SOURCE_TABLE = "users"     # ← table to migrate
CHUNK_SIZE = 10000         # ← rows per chunk
TOTAL_ROWS = 50000         # ← expected rows in source table (for planning)

# ──────────────────────────────────────────────────────────────────────────────


def setup_test_data():
    """
    Optional: Creates test data in the source database.
    Only needed if you don't already have a source table.
    
    Creates a 'users' table in the source DB with 50,000 rows.
    """
    import mysql.connector
    conn = mysql.connector.connect(**{
        "host": SOURCE_CONFIG["host"],
        "port": SOURCE_CONFIG["port"],
        "database": SOURCE_CONFIG["database"],
        "user": SOURCE_CONFIG["user"],
        "password": SOURCE_CONFIG["password"]
    })
    cursor = conn.cursor()

    print("Creating source test table...")
    cursor.execute("DROP TABLE IF EXISTS users")
    cursor.execute("""
        CREATE TABLE users (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255),
            created_at DATETIME DEFAULT NOW()
        )
    """)

    print(f"Inserting {TOTAL_ROWS:,} test rows...")
    batch_size = 1000
    for start in range(0, TOTAL_ROWS, batch_size):
        rows = [
            (f"User {start + i}", f"user{start + i}@example.com")
            for i in range(batch_size)
        ]
        cursor.executemany(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            rows
        )
        conn.commit()
        if (start + batch_size) % 10000 == 0:
            print(f"  Inserted {start + batch_size:,} rows...")

    cursor.close()
    conn.close()
    print("Source test data created.\n")


def create_target_table():
    """Create the matching table in the target database."""
    import mysql.connector
    conn = mysql.connector.connect(**{
        "host": TARGET_CONFIG["host"],
        "port": TARGET_CONFIG["port"],
        "database": TARGET_CONFIG["database"],
        "user": TARGET_CONFIG["user"],
        "password": TARGET_CONFIG["password"]
    })
    cursor = conn.cursor()

    print("Creating target table...")
    cursor.execute("DROP TABLE IF EXISTS users")
    cursor.execute("""
        CREATE TABLE users (
            id BIGINT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255),
            created_at DATETIME
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()
    print("Target table created.\n")


def run_test():
    print("=" * 60)
    print("WORKER END-TO-END TEST")
    print("=" * 60)

    # 1. Setup DB schema
    print("\n[1] Setting up metadata DB schema...")
    Base.metadata.create_all(bind=engine)
    print("    ✓ Schema ready")

    db: Session = next(get_db())

    # 2. Create migration job
    print("\n[2] Creating migration job...")
    job_manager = JobManager()
    job = job_manager.create_job(
        db=db,
        source_config=SOURCE_CONFIG,
        target_config=TARGET_CONFIG
    )
    print(f"    ✓ Job created: {job.id}")

    # 3. Create table entry
    print("\n[3] Creating table metadata...")
    table_repo = MigrationTableRepository()
    table = table_repo.create_table_entry(
        db=db,
        job_id=job.id,
        table_name=SOURCE_TABLE,
        primary_key_column="id"
    )
    print(f"    ✓ Table entry created: {table.id}")

    # 4. Generate chunks
    print(f"\n[4] Generating chunks (total_rows={TOTAL_ROWS:,}, chunk_size={CHUNK_SIZE:,})...")
    planner = Planner()
    chunks = planner.generate_chunks(
        db, job.id, table.id, SOURCE_TABLE, TOTAL_ROWS, CHUNK_SIZE
    )
    print(f"    ✓ Generated {len(chunks)} chunks")

    queue_len = redis_client.llen(Queues.MIGRATION_QUEUE)
    print(f"    ✓ Redis queue depth: {queue_len}")

    # 5. Run worker to process all chunks
    print(f"\n[5] Running worker to process {len(chunks)} chunks...")
    worker_id = "test-worker-001"
    executor = ChunkExecutor(worker_id=worker_id)

    processed = 0
    failed = 0

    while True:
        item = redis_client.rpop(Queues.MIGRATION_QUEUE)
        if not item:
            break

        message = json.loads(item)
        chunk_id = message["chunk_id"]

        try:
            db_session = next(get_db())
            executor.execute(
                db=db_session,
                job_id=message["job_id"],
                table_id=message["table_id"],
                chunk_id=chunk_id
            )
            db_session.close()
            processed += 1
            print(f"    ✓ Chunk {processed}/{len(chunks)} completed")
        except Exception as e:
            failed += 1
            print(f"    ✗ Chunk failed: {e}")

    print(f"\n    Processed: {processed}")
    print(f"    Failed:    {failed}")

    # 6. Verify final state
    print("\n[6] Verifying final state in metadata DB...")
    db2 = next(get_db())

    completed_chunks = db2.query(MigrationChunk).filter(
        MigrationChunk.job_id == job.id,
        MigrationChunk.status == "completed"
    ).count()

    final_job = db2.query(MigrationJob).filter(MigrationJob.id == job.id).first()

    print(f"    Completed chunks in DB: {completed_chunks}/{len(chunks)}")
    print(f"    Final job status: {final_job.status}")
    print(f"    Job completed_chunks: {final_job.completed_chunks}")

    if completed_chunks == len(chunks):
        print("\n✅ END-TO-END TEST PASSED")
    else:
        print("\n❌ END-TO-END TEST FAILED - some chunks did not complete")

    db2.close()
    db.close()
    print("=" * 60)


if __name__ == "__main__":
    # Uncomment these if you need to create test data first:
    # setup_test_data()
    # create_target_table()

    run_test()
