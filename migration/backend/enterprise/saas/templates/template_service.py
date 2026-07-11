"""
Migration Templates
File: migration/backend/enterprise/saas/templates/template_service.py

Save and reuse complete migration configurations.

A template captures:
  - Source and target DB types
  - Table and column mappings
  - Chunk configuration
  - Validation rules
  - Execution settings (max_workers, chunk_size strategy)

Use case:
  A company migrates from MySQL to PostgreSQL every quarter.
  They save a template after the first migration.
  Next quarter: create job → apply template → run.
  All mappings are pre-filled. Zero manual work.
"""

import uuid
import datetime
import json
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.shared.config.logging import logger


class TemplateService:

    def save_template(
        self,
        db:              Session,
        tenant_id:       str,
        created_by_id:   str,
        name:            str,
        description:     str = None,
        source_db_type:  str = "mysql",
        target_db_type:  str = "postgresql",
        table_mappings:  dict = None,
        chunk_config:    dict = None,
        validation_rules: list = None,
        execution_config: dict = None,
        tags:            list = None,
        is_public:       bool = False,
    ) -> dict:
        """Save a migration configuration as a reusable template."""
        tid = str(uuid.uuid4())
        now = datetime.datetime.utcnow()

        db.execute(
            text("""
                INSERT INTO migration_templates
                    (id, tenant_id, name, description,
                     source_db_type, target_db_type,
                     table_mappings, chunk_config,
                     validation_rules, execution_config,
                     tags, is_public, usage_count,
                     created_by_id, created_at, updated_at)
                VALUES
                    (:id, :tid, :name, :desc,
                     :sdb, :tdb,
                     :tmaps::jsonb, :cconf::jsonb,
                     :vrules::jsonb, :econf::jsonb,
                     :tags::jsonb, :pub, 0,
                     :by, :now, :now)
            """),
            {
                "id":     tid,
                "tid":    tenant_id,
                "name":   name,
                "desc":   description,
                "sdb":    source_db_type,
                "tdb":    target_db_type,
                "tmaps":  json.dumps(table_mappings or {}),
                "cconf":  json.dumps(chunk_config or {}),
                "vrules": json.dumps(validation_rules or []),
                "econf":  json.dumps(execution_config or {}),
                "tags":   json.dumps(tags or []),
                "pub":    is_public,
                "by":     created_by_id,
                "now":    now,
            }
        )
        db.commit()
        logger.info("Template saved", template_id=tid, name=name, tenant=tenant_id)
        return self.get_template(db, tid)

    def get_template(self, db: Session, template_id: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM migration_templates WHERE id=:id"),
            {"id": template_id}
        ).fetchone()
        return self._row(row) if row else None

    def list_templates(self, db: Session, tenant_id: str) -> List[dict]:
        """List templates for a tenant plus all public templates."""
        rows = db.execute(
            text("""
                SELECT * FROM migration_templates
                WHERE tenant_id=:tid OR is_public=TRUE
                ORDER BY usage_count DESC, created_at DESC
            """),
            {"tid": tenant_id}
        ).fetchall()
        return [self._row(r) for r in rows]

    def apply_template(
        self,
        db:          Session,
        template_id: str,
        job_id:      str,
    ) -> dict:
        """
        Apply a saved template to an existing migration job.
        Creates table mappings and sets chunk/execution config from the template.
        Returns a summary of what was applied.
        """
        template = self.get_template(db, template_id)
        if not template:
            raise ValueError(f"Template {template_id} not found")

        applied = {
            "template_id":    template_id,
            "template_name":  template["name"],
            "job_id":         job_id,
            "table_mappings_applied": 0,
            "chunk_config_applied":   False,
            "validation_rules_applied": 0,
        }

        # Apply table mappings to schema_table_mappings
        table_mappings = template.get("table_mappings") or {}
        if table_mappings and isinstance(table_mappings, dict):
            # Get the mapping project for this job if it exists
            proj = db.execute(
                text("SELECT id FROM mapping_projects WHERE migration_job_id=:jid LIMIT 1"),
                {"jid": job_id}
            ).fetchone()

            if proj:
                for src_table, mapping_def in table_mappings.items():
                    tgt_table = mapping_def.get("target", src_table)
                    db.execute(
                        text("""
                            INSERT INTO schema_table_mappings
                                (id, project_id, mapping_type, source_tables,
                                 target_tables, created_at, updated_at)
                            VALUES
                                (:id, :pid, 'single', :src::jsonb, :tgt::jsonb, :now, :now)
                            ON CONFLICT DO NOTHING
                        """),
                        {
                            "id":  str(uuid.uuid4()),
                            "pid": str(proj[0]),
                            "src": json.dumps([src_table]),
                            "tgt": json.dumps([tgt_table]),
                            "now": datetime.datetime.utcnow(),
                        }
                    )
                applied["table_mappings_applied"] = len(table_mappings)

        db.commit()

        # Increment usage count
        db.execute(
            text("UPDATE migration_templates SET usage_count=usage_count+1, updated_at=:now WHERE id=:id"),
            {"now": datetime.datetime.utcnow(), "id": template_id}
        )
        db.commit()

        logger.info("Template applied", template_id=template_id, job_id=job_id)
        return applied

    def delete_template(self, db: Session, template_id: str, tenant_id: str) -> dict:
        db.execute(
            text("DELETE FROM migration_templates WHERE id=:id AND tenant_id=:tid"),
            {"id": template_id, "tid": tenant_id}
        )
        db.commit()
        return {"deleted": template_id}

    def _row(self, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d
