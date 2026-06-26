"""
Schema Mapping Repository
File: migration/backend/schema_mapping_service/app/repositories/mapping_repository.py

All database read/write operations for the schema mapping service.
Uses raw SQL (via SQLAlchemy text()) since we have no ORM models for
these new tables yet — keeps it simple and compatible with your existing setup.
"""

import uuid
import datetime
import json
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.shared.config.logging import logger


class MappingRepository:

    # ── Schema Versions ───────────────────────────────────────────────────────

    def save_schema_version(
        self,
        db: Session,
        tenant_id: str,
        name: str,
        db_type: str,
        schema_data: dict,
        version_label: str = None,
        source_type: str = "live_db",
        notes: str = None,
    ) -> dict:
        vid = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO schema_versions
                    (id, tenant_id, name, db_type, version_label, schema_data,
                     source_type, notes, created_at)
                VALUES
                    (:id, :tid, :name, :db_type, :vlabel, :data::jsonb,
                     :stype, :notes, :now)
            """),
            {
                "id": vid, "tid": tenant_id, "name": name, "db_type": db_type,
                "vlabel": version_label, "data": json.dumps(schema_data),
                "stype": source_type, "notes": notes, "now": datetime.datetime.utcnow(),
            }
        )
        db.commit()
        return self.get_schema_version(db, vid)

    def get_schema_version(self, db: Session, version_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM schema_versions WHERE id = :id"),
            {"id": version_id}
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_schema_versions(self, db: Session, tenant_id: str = "local") -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM schema_versions WHERE tenant_id = :tid ORDER BY created_at DESC"),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Mapping Projects ──────────────────────────────────────────────────────

    def create_project(
        self,
        db: Session,
        tenant_id: str,
        name: str,
        source_schema_id: str,
        target_schema_id: str,
        description: str = None,
    ) -> dict:
        pid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                INSERT INTO mapping_projects
                    (id, tenant_id, name, description, source_schema_id,
                     target_schema_id, status, created_at, updated_at)
                VALUES
                    (:id, :tid, :name, :desc, :src_id, :tgt_id, 'draft', :now, :now)
            """),
            {
                "id": pid, "tid": tenant_id, "name": name, "desc": description,
                "src_id": source_schema_id, "tgt_id": target_schema_id, "now": now,
            }
        )
        db.commit()
        return self.get_project(db, pid)

    def get_project(self, db: Session, project_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM mapping_projects WHERE id = :id"),
            {"id": project_id}
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_projects(self, db: Session, tenant_id: str = "local") -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM mapping_projects WHERE tenant_id = :tid ORDER BY created_at DESC"),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_project_status(self, db: Session, project_id: str, status: str):
        db.execute(
            text("UPDATE mapping_projects SET status=:s, updated_at=:now WHERE id=:id"),
            {"s": status, "now": datetime.datetime.utcnow(), "id": project_id}
        )
        db.commit()

    def save_dry_run_result(self, db: Session, project_id: str, result: dict):
        db.execute(
            text("UPDATE mapping_projects SET dry_run_result=:r::jsonb, updated_at=:now WHERE id=:id"),
            {"r": json.dumps(result), "now": datetime.datetime.utcnow(), "id": project_id}
        )
        db.commit()

    def save_migration_plan(self, db: Session, project_id: str, plan: dict):
        db.execute(
            text("UPDATE mapping_projects SET migration_plan=:p::jsonb, updated_at=:now WHERE id=:id"),
            {"p": json.dumps(plan), "now": datetime.datetime.utcnow(), "id": project_id}
        )
        db.commit()

    # ── Table Mappings ────────────────────────────────────────────────────────

    def save_table_mapping(
        self,
        db: Session,
        project_id: str,
        mapping_type: str,
        source_tables: List[str],
        target_tables: List[str],
        join_condition: str = None,
        notes: str = None,
    ) -> dict:
        mid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        db.execute(
            text("""
                INSERT INTO schema_table_mappings
                    (id, project_id, mapping_type, source_tables, target_tables,
                     join_condition, notes, created_at, updated_at)
                VALUES
                    (:id, :pid, :mtype, :src::jsonb, :tgt::jsonb,
                     :jc, :notes, :now, :now)
            """),
            {
                "id": mid, "pid": project_id, "mtype": mapping_type,
                "src": json.dumps(source_tables), "tgt": json.dumps(target_tables),
                "jc": join_condition, "notes": notes, "now": now,
            }
        )
        db.commit()
        return self.get_table_mapping(db, mid)

    def get_table_mapping(self, db: Session, mapping_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM schema_table_mappings WHERE id = :id"),
            {"id": mapping_id}
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_table_mappings(self, db: Session, project_id: str) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM schema_table_mappings WHERE project_id=:pid ORDER BY created_at"),
            {"pid": project_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_table_mapping(self, db: Session, mapping_id: str):
        db.execute(text("DELETE FROM schema_table_mappings WHERE id=:id"), {"id": mapping_id})
        db.commit()

    # ── Column Mappings ───────────────────────────────────────────────────────

    def save_column_mapping(
        self,
        db: Session,
        table_mapping_id: str,
        source_table: str,
        source_column: str,
        source_type: str,
        target_table: str,
        target_column: str,
        target_type: str,
        mapping_kind: str = "direct",
        mapping_config: dict = None,
        conversion_safety: str = None,
        requires_cast: bool = False,
        cast_expression: str = None,
    ) -> dict:
        cid = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO schema_column_mappings
                    (id, table_mapping_id, source_table, source_column, source_type,
                     target_table, target_column, target_type, mapping_kind,
                     mapping_config, conversion_safety, requires_cast,
                     cast_expression, created_at)
                VALUES
                    (:id, :tmid, :stbl, :scol, :styp,
                     :ttbl, :tcol, :ttyp, :kind,
                     :cfg::jsonb, :safety, :cast_req,
                     :cast_expr, :now)
            """),
            {
                "id": cid, "tmid": table_mapping_id,
                "stbl": source_table, "scol": source_column, "styp": source_type,
                "ttbl": target_table, "tcol": target_column, "ttyp": target_type,
                "kind": mapping_kind,
                "cfg": json.dumps(mapping_config or {}),
                "safety": conversion_safety, "cast_req": requires_cast,
                "cast_expr": cast_expression, "now": datetime.datetime.utcnow(),
            }
        )
        db.commit()
        return self.get_column_mapping(db, cid)

    def get_column_mapping(self, db: Session, col_mapping_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM schema_column_mappings WHERE id=:id"),
            {"id": col_mapping_id}
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_column_mappings(self, db: Session, table_mapping_id: str) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM schema_column_mappings WHERE table_mapping_id=:tmid ORDER BY created_at"),
            {"tmid": table_mapping_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_column_mapping(self, db: Session, col_mapping_id: str):
        db.execute(text("DELETE FROM schema_column_mappings WHERE id=:id"), {"id": col_mapping_id})
        db.commit()

    def bulk_save_column_mappings(
        self,
        db: Session,
        table_mapping_id: str,
        mappings: List[dict],
    ) -> List[dict]:
        """Save multiple column mappings at once (from recommendation acceptance)."""
        saved = []
        for m in mappings:
            saved.append(self.save_column_mapping(
                db=db,
                table_mapping_id=table_mapping_id,
                source_table=m.get("source_table", ""),
                source_column=m.get("source_column", ""),
                source_type=m.get("source_type", ""),
                target_table=m.get("target_table", ""),
                target_column=m.get("target_column", ""),
                target_type=m.get("target_type", ""),
                mapping_kind=m.get("mapping_kind", "direct"),
                mapping_config=m.get("mapping_config"),
                conversion_safety=m.get("conversion_safety"),
                requires_cast=m.get("requires_cast", False),
                cast_expression=m.get("cast_expression"),
            ))
        return saved

    # ── Recommendations ───────────────────────────────────────────────────────

    def save_recommendations(self, db: Session, project_id: str, recs: List[dict]) -> int:
        """Delete existing recs and save new batch."""
        db.execute(
            text("DELETE FROM schema_recommendations WHERE project_id=:pid"),
            {"pid": project_id}
        )
        now = datetime.datetime.utcnow()
        for r in recs:
            db.execute(
                text("""
                    INSERT INTO schema_recommendations
                        (id, project_id, rec_type, source_ref, target_ref,
                         confidence, reason, accepted, created_at)
                    VALUES
                        (:id, :pid, :rtype, :src, :tgt, :conf, :reason, NULL, :now)
                """),
                {
                    "id": str(uuid.uuid4()), "pid": project_id,
                    "rtype": r["rec_type"], "src": r["source_ref"],
                    "tgt": r["target_ref"], "conf": r["confidence"],
                    "reason": r["reason"], "now": now,
                }
            )
        db.commit()
        return len(recs)

    def list_recommendations(self, db: Session, project_id: str) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM schema_recommendations WHERE project_id=:pid ORDER BY confidence DESC"),
            {"pid": project_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def accept_recommendations(self, db: Session, project_id: str, rec_ids: List[str]):
        """Accept specific recommendations by their composite ID (source_ref→target_ref)."""
        for rec_id in rec_ids:
            if "→" in rec_id:
                src, tgt = rec_id.split("→", 1)
                db.execute(
                    text("""
                        UPDATE schema_recommendations
                        SET accepted = TRUE
                        WHERE project_id=:pid AND source_ref=:src AND target_ref=:tgt
                    """),
                    {"pid": project_id, "src": src, "tgt": tgt}
                )
        db.commit()

    def reject_recommendations(self, db: Session, project_id: str, rec_ids: List[str]):
        for rec_id in rec_ids:
            if "→" in rec_id:
                src, tgt = rec_id.split("→", 1)
                db.execute(
                    text("""
                        UPDATE schema_recommendations
                        SET accepted = FALSE
                        WHERE project_id=:pid AND source_ref=:src AND target_ref=:tgt
                    """),
                    {"pid": project_id, "src": src, "tgt": tgt}
                )
        db.commit()

    # ── Generated Scripts ─────────────────────────────────────────────────────

    def save_script(
        self,
        db: Session,
        project_id: str,
        script_type: str,
        target_table: str,
        content: str,
        filename: str,
    ) -> dict:
        sid = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO generated_scripts
                    (id, project_id, script_type, target_table, content, filename, created_at)
                VALUES
                    (:id, :pid, :stype, :tbl, :content, :fname, :now)
            """),
            {
                "id": sid, "pid": project_id, "stype": script_type,
                "tbl": target_table, "content": content,
                "fname": filename, "now": datetime.datetime.utcnow(),
            }
        )
        db.commit()
        return {"id": sid, "project_id": project_id, "script_type": script_type,
                "target_table": target_table, "filename": filename}

    def list_scripts(self, db: Session, project_id: str) -> List[dict]:
        rows = db.execute(
            text("SELECT id, project_id, script_type, target_table, filename, created_at "
                 "FROM generated_scripts WHERE project_id=:pid ORDER BY created_at DESC"),
            {"pid": project_id}
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_script_content(self, db: Session, script_id: str) -> Optional[str]:
        row = db.execute(
            text("SELECT content FROM generated_scripts WHERE id=:id"),
            {"id": script_id}
        ).fetchone()
        return row[0] if row else None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _row_to_dict(self, row) -> dict:
        if row is None:
            return {}
        d = dict(row._mapping)
        # Parse JSONB fields that come back as strings in some drivers
        for key in ("schema_data", "dry_run_result", "migration_plan",
                    "mapping_config", "source_tables", "target_tables"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except Exception:
                    pass
        # Convert UUIDs and datetimes to strings
        for key, val in d.items():
            if hasattr(val, 'hex'):        # UUID
                d[key] = str(val)
            elif hasattr(val, 'isoformat'): # datetime
                d[key] = val.isoformat()
        return d
