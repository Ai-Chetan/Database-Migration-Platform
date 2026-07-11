from typing import List, Optional
from sqlalchemy.orm import Session
from backend.control_plane.app.models.migration import MigrationTable
import uuid

from backend.control_plane.app.models.migration import MigrationTable


class MigrationTableRepository:

    def create_table_entry(
        self,
        db,
        job_id,
        table_name,
        primary_key_column="id"
    ):
        table = MigrationTable(
            job_id=job_id,
            table_name=table_name,
            primary_key_column=primary_key_column,
            status="pending"
        )

        db.add(table)
        db.commit()
        db.refresh(table)

        return table

    def update_table_status(self, db: Session, table_id: str, status: str) -> Optional[MigrationTable]:
        table = db.query(MigrationTable).filter(MigrationTable.id == table_id).first()
        if table:
            table.status = status
            db.commit()
            db.refresh(table)
        return table

    def get_job_tables(self, db: Session, job_id: str) -> List[MigrationTable]:
        return db.query(MigrationTable).filter(MigrationTable.job_id == job_id).all()
