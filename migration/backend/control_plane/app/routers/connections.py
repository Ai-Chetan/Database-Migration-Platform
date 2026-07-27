"""
Connections Router
File: migration/backend/control_plane/app/routers/connections.py

NEW FILE — the other cause of the startup warning:
    [WARN] Control Plane routers not loaded: cannot import name 'connections'
    from 'backend.control_plane.app.routers'
The underlying logic (ConnectionManager) was already fully built —
encryption, testing, rotation, CRUD — just with no HTTP layer. This file
is that HTTP layer, wired to the real connection_registry table.

Endpoints:
    POST   /connections              → register a new connection (tests + encrypts password)
    GET    /connections              → list connections for the tenant
    GET    /connections/{id}         → get one connection (no password)
    POST   /connections/{id}/test    → re-test an existing connection
    POST   /connections/{id}/rotate-password → rotate credentials
    DELETE /connections/{id}         → delete a connection
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser
from backend.enterprise.connection_manager.connection_manager import ConnectionManager
from backend.shared.config.logging import logger

router = APIRouter(prefix="/connections", tags=["Connections"])

conn_mgr = ConnectionManager()


class RegisterConnectionRequest(BaseModel):
    name:            str
    db_type:         str   # mysql | postgresql | oracle | sqlserver | mariadb | sqlite
    host:            str
    port:            int
    database_name:   str
    username:        str
    password:        str
    ssl_enabled:     bool = False
    pool_size:       int = 5
    connect_timeout: int = 30
    query_timeout:   int = 300
    extra_params:    Optional[Dict[str, Any]] = None
    test_before_save: bool = True


class RotatePasswordRequest(BaseModel):
    new_password: str


@router.post("", summary="Register a new database connection")
def register_connection(
    req:  RegisterConnectionRequest,
    user: CurrentUser = Depends(require_permission("connections:create")),
    db:   Session = Depends(get_db),
):
    """
    Registers a source or target database connection. Password is
    encrypted before storage (never stored in plaintext). By default the
    connection is tested before saving — set test_before_save=false to
    skip this (e.g. for a target database that doesn't exist yet).
    """
    try:
        result = conn_mgr.register(
            db=db, tenant_id=user.tenant_id, name=req.name, db_type=req.db_type,
            host=req.host, port=req.port, database_name=req.database_name,
            username=req.username, password=req.password,
            ssl_enabled=req.ssl_enabled, pool_size=req.pool_size,
            connect_timeout=req.connect_timeout, query_timeout=req.query_timeout,
            extra_params=req.extra_params, test_before_save=req.test_before_save,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Connection registered", name=req.name, db_type=req.db_type, by=user.user_id)
    return result


@router.get("", summary="List connections")
def list_connections(
    user: CurrentUser = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    """Lists all connections for the caller's tenant (passwords never returned)."""
    return {"connections": conn_mgr.list(db, tenant_id=user.tenant_id)}


@router.get("/{connection_id}", summary="Get one connection")
def get_connection(
    connection_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    result = conn_mgr.get(db, connection_id)
    if not result:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if not user.can("*") and result.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Connection not found.")
    return result


@router.post("/{connection_id}/test", summary="Test an existing connection")
def test_connection(
    connection_id: str,
    user: CurrentUser = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    """Re-tests a saved connection using its stored (decrypted) credentials."""
    result = conn_mgr.get(db, connection_id)
    if not result:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if not user.can("*") and result.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Connection not found.")

    return conn_mgr.test(db, connection_id)


@router.post("/{connection_id}/rotate-password", summary="Rotate connection credentials")
def rotate_password(
    connection_id: str,
    req: RotatePasswordRequest,
    user: CurrentUser = Depends(require_permission("connections:update")),
    db:   Session = Depends(get_db),
):
    result = conn_mgr.get(db, connection_id)
    if not result:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if not user.can("*") and result.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Connection not found.")

    return conn_mgr.rotate_password(db, connection_id, req.new_password)


@router.delete("/{connection_id}", summary="Delete a connection")
def delete_connection(
    connection_id: str,
    user: CurrentUser = Depends(require_permission("connections:delete")),
    db:   Session = Depends(get_db),
):
    result = conn_mgr.get(db, connection_id)
    if not result:
        raise HTTPException(status_code=404, detail="Connection not found.")
    if not user.can("*") and result.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=404, detail="Connection not found.")

    return conn_mgr.delete(db, connection_id)
