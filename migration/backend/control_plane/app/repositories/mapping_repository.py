from typing import List, Optional
from sqlalchemy.orm import Session
from backend.control_plane.app.models.migration import Mapping
import uuid

class MappingRepository:
    def save_table_mapping(self, db: Session, table_id: str, source: str, target: str, config: dict = None) -> Mapping:
        mapping = Mapping(
            id=str(uuid.uuid4()),
            table_id=table_id,
            mapping_type="TABLE",
            source=source,
            target=target,
            config=config
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        return mapping

    def save_column_mapping(self, db: Session, table_id: str, source: str, target: str, config: dict = None) -> Mapping:
        mapping = Mapping(
            id=str(uuid.uuid4()),
            table_id=table_id,
            mapping_type="COLUMN",
            source=source,
            target=target,
            config=config
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        return mapping

    def get_mappings(self, db: Session, table_id: str) -> List[Mapping]:
        return db.query(Mapping).filter(Mapping.table_id == table_id).all()
