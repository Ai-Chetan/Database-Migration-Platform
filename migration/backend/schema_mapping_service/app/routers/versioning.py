"""
Schema Versioning Router
File: migration/backend/schema_mapping_service/app/routers/versioning.py

Tracks schema evolution over time. Every time you discover or import
a schema, a new version is saved. This router lets you compare versions,
list history, and track what changed between snapshots.

Endpoints:
    GET  /schemas/{id}/versions           → list all versions of a named schema
    POST /schemas/compare-versions        → diff two versions of the same schema
    GET  /schemas/{id}/changelog          → human-readable changelog between versions
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

from backend.shared.config.database import get_db
from backend.schema_mapping_service.app.comparison.schema_comparator import SchemaComparator
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository

router     = APIRouter(tags=["Schema Versioning"])
repo       = MappingRepository()
comparator = SchemaComparator()


class CompareVersionsRequest(BaseModel):
    version_id_before: str
    version_id_after:  str


@router.get("/schemas/versions/list", summary="List all schema versions grouped by name")
def list_all_versions(tenant_id: str = "local", db: Session = Depends(get_db)):
    """
    Returns all saved schema versions grouped by schema name.
    Each entry shows the version label, source type, table count, and when it was created.
    Useful for seeing the history of a schema over time.
    """
    rows = db.execute(
        text("""
            SELECT id, name, db_type, version_label, source_type,
                   jsonb_array_length(schema_data->'tables') AS table_count,
                   created_at
            FROM schema_versions
            WHERE tenant_id = :tid
            ORDER BY name, created_at DESC
        """),
        {"tid": tenant_id}
    ).fetchall()

    grouped = {}
    for row in rows:
        d = dict(row._mapping)
        if hasattr(d.get("id"), "hex"):
            d["id"] = str(d["id"])
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        name = d["name"]
        grouped.setdefault(name, []).append(d)

    return {"schemas": grouped, "total_versions": len(rows)}


@router.post("/schemas/compare-versions", summary="Compare two versions of a schema")
def compare_versions(req: CompareVersionsRequest, db: Session = Depends(get_db)):
    """
    Compares two saved schema versions (typically before and after a change)
    and returns the full structured diff.

    Use this to:
    - Understand what changed between production snapshots
    - Audit schema evolution over time
    - Verify that a migration produced the expected schema changes

    Returns the same diff format as POST /compare but scoped to two
    specific saved versions rather than the current source/target schemas.
    """
    before = repo.get_schema_version(db, req.version_id_before)
    after  = repo.get_schema_version(db, req.version_id_after)

    if not before:
        raise HTTPException(status_code=404, detail=f"Version {req.version_id_before} not found")
    if not after:
        raise HTTPException(status_code=404, detail=f"Version {req.version_id_after} not found")

    diff = comparator.compare(
        source_schema=before["schema_data"],
        target_schema=after["schema_data"],
    )

    return {
        "before": {
            "id":            req.version_id_before,
            "name":          before.get("name"),
            "version_label": before.get("version_label"),
            "created_at":    before.get("created_at"),
        },
        "after": {
            "id":            req.version_id_after,
            "name":          after.get("name"),
            "version_label": after.get("version_label"),
            "created_at":    after.get("created_at"),
        },
        "diff": diff.to_dict(),
    }


@router.get("/schemas/{schema_id}/changelog", summary="Get human-readable changelog for a schema")
def get_changelog(schema_id: str, db: Session = Depends(get_db)):
    """
    Compares this schema version against the previous version with the same name
    and returns a human-readable changelog.

    Example output:
    {
      "changelog": [
        "Table 'orders': column 'total' type changed from INT to DECIMAL(18,4) [lossy]",
        "Table 'users': column 'cust_name' likely renamed to 'customer_name' (87% similarity)",
        "Table 'products': new column 'sku_code' added",
        "Table 'legacy_table': removed"
      ]
    }
    """
    current = repo.get_schema_version(db, schema_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"Schema {schema_id} not found")

    # Find the previous version with the same name
    prev_row = db.execute(
        text("""
            SELECT id FROM schema_versions
            WHERE name = :name AND tenant_id = :tid AND id != :id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"name": current["name"], "tid": current["tenant_id"], "id": schema_id}
    ).fetchone()

    if not prev_row:
        return {
            "schema_id":     schema_id,
            "name":          current["name"],
            "version_label": current.get("version_label"),
            "changelog":     ["No previous version found — this is the first version"],
            "is_first_version": True,
        }

    previous = repo.get_schema_version(db, str(prev_row[0]))
    diff     = comparator.compare(
        source_schema=previous["schema_data"],
        target_schema=current["schema_data"],
    )

    changelog = []

    for tname in diff.tables_added:
        changelog.append(f"Table '{tname}': ADDED")

    for tname in diff.tables_removed:
        changelog.append(f"Table '{tname}': REMOVED")

    for td in diff.tables_changed:
        for cd in td.column_diffs:
            if cd.status == "added":
                changelog.append(
                    f"Table '{td.table_name}': column '{cd.column_name}' ADDED "
                    f"(type: {cd.new_type})"
                )
            elif cd.status == "removed":
                changelog.append(
                    f"Table '{td.table_name}': column '{cd.column_name}' REMOVED"
                )
            elif cd.status == "renamed":
                changelog.append(
                    f"Table '{td.table_name}': column '{cd.old_name}' likely RENAMED to "
                    f"'{cd.column_name}' {cd.change_details[0] if cd.change_details else ''}"
                )
            elif cd.status == "changed":
                for detail in cd.change_details:
                    changelog.append(
                        f"Table '{td.table_name}': column '{cd.column_name}' — {detail}"
                    )
        if td.pk_changed:
            changelog.append(f"Table '{td.table_name}': PRIMARY KEY changed")
        for fk_change in td.fk_changes:
            changelog.append(f"Table '{td.table_name}': {fk_change}")

    if not changelog:
        changelog.append("No schema changes detected between versions")

    return {
        "schema_id":          schema_id,
        "name":               current["name"],
        "current_version":    current.get("version_label"),
        "previous_version":   previous.get("version_label"),
        "current_created_at": current.get("created_at"),
        "risk_level":         diff.risk_level,
        "summary":            diff.summary,
        "changelog":          changelog,
    }
