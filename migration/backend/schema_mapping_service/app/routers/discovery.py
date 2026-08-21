"""
Schema Discovery Router
File: migration/backend/schema_mapping_service/app/routers/discovery.py

Endpoints:
    POST /schemas/discover        → discover live DB schema
    POST /schemas/import-file     → import from text file
    GET  /schemas                 → list all saved schema versions
    GET  /schemas/{id}            → get one schema version (full data)
    GET  /schemas/{id}/tables     → list table names only

CHANGES IN THIS VERSION (Stage 1 audit fix):
  Same architectural gap as the rest of Phase B: zero authentication on
  an endpoint that accepts raw database credentials in the request body
  and connects to a live database (discover_schema) - and no ownership
  check on get_schema/list_schema_tables, so any authenticated user could
  read another tenant's full discovered schema (table/column names,
  row counts) by guessing a UUID. tenant_id now comes from the
  authenticated user, not a client-supplied request field.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.schema_mapping_service.app.discovery.schema_discovery import (
    SchemaDiscovery, parse_schema_file
)
from backend.schema_mapping_service.app.repositories.mapping_repository import MappingRepository
from backend.schema_mapping_service.app.schemas.schemas import (
    DiscoverRequest, FileImportRequest, SchemaVersionResponse
)
from backend.enterprise.connection_manager.connection_manager import ConnectionManager
from backend.shared.utils.db_config import normalize_db_config

router  = APIRouter(prefix="/schemas", tags=["Schema Discovery"])
repo    = MappingRepository()
conn_mgr = ConnectionManager()


def _owned_schema(db: Session, schema_id: str, user: CurrentUser) -> dict:
    version = repo.get_schema_version(db, schema_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Schema {schema_id} not found")
    if not user.can("*") and version.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail=f"Schema {schema_id} not found")
    return version


@router.post("/discover", summary="Discover schema from a live database")
def discover_schema(
    req: DiscoverRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:write")),
):
    """
    Connect to a live database and discover its full schema.
    Saves the result as a new schema version in the metadata DB.

    Supply EITHER connection_id (preferred - resolves a saved Connection's
    credentials server-side) OR config directly:
    {
      "name": "production_mysql",
      "connection_id": "5b8e6810-..."
    }
    OR
    {
      "name": "production_mysql",
      "config": {
        "engine": "mysql",
        "host": "localhost",
        "port": 3306,
        "database": "mydb",
        "user": "root",
        "password": "secret"
      }
    }
    """
    if req.connection_id:
        conn = conn_mgr.get(db, req.connection_id)
        if not conn or (not user.can("*") and conn["tenant_id"] != user.tenant_id):
            raise HTTPException(status_code=404, detail=f"Connection {req.connection_id} not found")
        config = conn_mgr.get_config(db, req.connection_id)
        if not config:
            raise HTTPException(status_code=400, detail="Could not resolve connection credentials.")
    elif req.config:
        # Same user/username, database/database_name key-name normalization
        # used at job creation - see shared/utils/db_config.py.
        try:
            config = normalize_db_config(req.config)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="Either connection_id or config must be provided.")

    try:
        discoverer  = SchemaDiscovery(config=config)
        schema_data = discoverer.discover()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Discovery failed: {e}")

    version = repo.save_schema_version(
        db=db,
        tenant_id=user.tenant_id,
        name=req.name,
        db_type=config.get("engine", "unknown"),
        schema_data=schema_data,
        version_label=req.version_label,
        source_type="live_db",
        notes=req.notes,
    )
    version["table_count"] = len(schema_data.get("tables", {}))
    return version


@router.post("/import-file", summary="Import schema from text file content")
def import_schema_file(
    req: FileImportRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:write")),
):
    """
    Parse a plain-text schema file (original tool format) and save it.

    The file content should follow:
        Table: users
          id INT AUTO_INCREMENT PRIMARY KEY
          name VARCHAR(255) NOT NULL
    """
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(req.file_content)
        tmp_path = f.name

    try:
        schema_data = parse_schema_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not schema_data.get("tables"):
        raise HTTPException(status_code=400, detail="No tables found in file content")

    version = repo.save_schema_version(
        db=db,
        tenant_id=user.tenant_id,
        name=req.name,
        db_type="file",
        schema_data=schema_data,
        version_label=req.version_label,
        source_type="file",
        notes=req.notes,
    )
    version["table_count"] = len(schema_data.get("tables", {}))
    return version


@router.get("", summary="List saved schema versions for your tenant")
def list_schemas(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:read")),
):
    versions = repo.list_schema_versions(db, user.tenant_id)
    for v in versions:
        v["table_count"] = len(v.get("schema_data", {}).get("tables", {}))
        v.pop("schema_data", None)   # Don't return full schema in list
    return versions


@router.get("/{schema_id}", summary="Get full schema version")
def get_schema(
    schema_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:read")),
):
    version = _owned_schema(db, schema_id, user)
    version["table_count"] = len(version.get("schema_data", {}).get("tables", {}))
    return version


@router.get("/{schema_id}/tables", summary="List table names in a schema")
def list_schema_tables(
    schema_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("schema:read")),
):
    version = _owned_schema(db, schema_id, user)

    schema_data = version.get("schema_data", {})
    tables = []
    for tname, tdata in schema_data.get("tables", {}).items():
        tables.append({
            "table_name":  tname,
            "column_count": len(tdata.get("columns", {})),
            "row_count":   tdata.get("row_count", 0),
            "pk_columns":  tdata.get("primary_keys", []),
            "fk_count":    len(tdata.get("foreign_keys", [])),
            "index_count": len(tdata.get("indexes", [])),
        })
    return {"schema_id": schema_id, "tables": tables}
