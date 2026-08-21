"""
Connection Manager Router
File: migration/backend/enterprise/routers/connections.py

Endpoints:
    POST   /connections              → register new connection
    GET    /connections              → list connections (caller's tenant)
    GET    /connections/{id}         → get one connection
    PUT    /connections/{id}         → update connection details (NEW)
    POST   /connections/{id}/test    → test an existing connection
    POST   /connections/test-raw     → test without saving
    PUT    /connections/{id}/rotate  → rotate password
    DELETE /connections/{id}         → deactivate connection

SECURITY FIX IN THIS VERSION:
    None of these endpoints had ANY authentication - no Depends(get_current_
    user) or require_permission() anywhere in this file. Combined with
    every endpoint defaulting to (or accepting as a client-supplied query
    param!) tenant_id="local", this meant:
      1. Anyone who could reach the API at all - no token required - could
         list, create, test, rotate the password on, and delete every
         database connection for every tenant on the platform.
      2. list_connections took tenant_id as a query parameter the CALLER
         supplied, so even with auth added naively, a client could pass
         ?tenant_id=<any other tenant> and see connections that aren't
         theirs.
    Fixed: every endpoint now requires a valid token via require_permission
    (connections:read for read-only actions, connections:write for
    create/update/rotate/delete - matches the permission strings already
    seeded in db_migrations/017_seed_roles.sql), tenant scoping now always
    comes from the authenticated user's token (user.tenant_id), never from
    a client-supplied parameter, and every single-connection lookup
    verifies the connection actually belongs to the caller's tenant (404,
    not 403, if not - avoids confirming a connection ID exists in another
    tenant). platform_admin (user.can("*")) can see/manage all tenants',
    matching the pattern used in enterprise/routers/jobs.py and tenants.py.

ALSO ADDED: PUT /connections/{id}. The frontend's edit-connection form
    already existed and submitted here, but this route never existed -
    every edit attempt 404'd. connection_manager.py already had a fully
    working update() method; it just had no HTTP route calling it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.enterprise.connection_manager.connection_manager import ConnectionManager
from backend.enterprise.security.rbac.auth import get_current_user, require_permission, CurrentUser

router  = APIRouter(prefix="/connections", tags=["Connection Manager"])
manager = ConnectionManager()


class RegisterConnectionRequest(BaseModel):
    name:             str
    db_type:          str              # mysql | postgresql | oracle | sqlserver
    host:             str
    port:             int
    database_name:    str
    username:         str
    password:         str
    ssl_enabled:      bool = False
    pool_size:        int  = 5
    connect_timeout:  int  = 30
    query_timeout:    int  = 300
    extra_params:     Optional[Dict[str, Any]] = None
    test_before_save: bool = True


class UpdateConnectionRequest(BaseModel):
    """All fields optional - only supplied fields are changed. Password is
    intentionally NOT accepted here; use PUT /{id}/rotate for that, which
    re-tests the new password before committing."""
    name:             Optional[str] = None
    host:             Optional[str] = None
    port:             Optional[int] = None
    database_name:    Optional[str] = None
    username:         Optional[str] = None
    ssl_enabled:      Optional[bool] = None
    pool_size:        Optional[int] = None
    connect_timeout:  Optional[int] = None
    query_timeout:    Optional[int] = None


class TestRawRequest(BaseModel):
    db_type:         str
    host:            str
    port:            int
    database_name:   str
    username:        str
    password:        str
    connect_timeout: int = 10


class RotatePasswordRequest(BaseModel):
    new_password:       str
    test_before_rotate: bool = True


def _get_owned_or_404(db: Session, connection_id: str, user: CurrentUser) -> dict:
    """Fetch a connection and verify it belongs to the caller's tenant.
    Returns 404 (not 403) for a connection that exists but belongs to
    another tenant, so this endpoint never confirms IDs from other
    tenants exist."""
    conn = manager.get(db, connection_id)
    if not conn or (not user.can("*") and conn["tenant_id"] != user.tenant_id):
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")
    return conn


@router.post("", summary="Register a new database connection")
def register_connection(
    req:  RegisterConnectionRequest,
    user: CurrentUser = Depends(require_permission("connections:write")),
    db:   Session = Depends(get_db),
):
    """
    Register a database connection with encrypted password storage.
    The password is AES-256 encrypted before being saved to PostgreSQL.
    The plaintext password is NEVER logged or returned.

    Set test_before_save=true (default) to verify the connection is reachable
    before saving. The API returns an error if the connection test fails.

    Returns the connection record WITHOUT the password.
    """
    try:
        result = manager.register(
            db=db,
            tenant_id=user.tenant_id,
            name=req.name,
            db_type=req.db_type,
            host=req.host,
            port=req.port,
            database_name=req.database_name,
            username=req.username,
            password=req.password,
            ssl_enabled=req.ssl_enabled,
            pool_size=req.pool_size,
            connect_timeout=req.connect_timeout,
            query_timeout=req.query_timeout,
            extra_params=req.extra_params,
            test_before_save=req.test_before_save,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", summary="List all registered connections (no passwords)")
def list_connections(
    user: CurrentUser = Depends(require_permission("connections:read")),
    db:   Session = Depends(get_db),
):
    """
    Returns all active connections for the caller's tenant (all tenants
    for platform_admin). Passwords are NEVER returned. Use /test to verify
    a connection is alive.
    """
    return manager.list(db, user.tenant_id)


@router.get("/{connection_id}", summary="Get one connection record")
def get_connection(
    connection_id: str,
    user: CurrentUser = Depends(require_permission("connections:read")),
    db:   Session = Depends(get_db),
):
    return _get_owned_or_404(db, connection_id, user)


@router.put("/{connection_id}", summary="Update connection details")
def update_connection(
    connection_id: str,
    req:  UpdateConnectionRequest,
    user: CurrentUser = Depends(require_permission("connections:write")),
    db:   Session = Depends(get_db),
):
    """
    Updates name/host/port/database_name/username/ssl/pool settings.
    Does NOT change the password - use PUT /{id}/rotate for that (it
    re-tests the new password before committing, which a generic update
    endpoint intentionally does not do for arbitrary field combinations).
    """
    _get_owned_or_404(db, connection_id, user)
    return manager.update(db, connection_id, **req.model_dump(exclude_unset=True))


@router.post("/{connection_id}/test", summary="Test an existing registered connection")
def test_connection(
    connection_id: str,
    user: CurrentUser = Depends(require_permission("connections:read")),
    db:   Session = Depends(get_db),
):
    """
    Decrypts the stored password and attempts a live connection.
    Updates last_tested_at and last_test_status in the registry.

    Returns:
      - success: true/false
      - db_version: database version string
      - latency_ms: round-trip time in milliseconds
      - error: error message if failed
    """
    _get_owned_or_404(db, connection_id, user)
    return manager.test(db, connection_id)


@router.post("/test-raw", summary="Test a connection without saving it")
def test_raw_connection(
    req:  TestRawRequest,
    user: CurrentUser = Depends(require_permission("connections:read")),
):
    """
    Test a connection with provided credentials without saving anything to DB.
    Useful for the frontend connection wizard before registration.
    """
    return manager.test_connection_raw(
        db_type=req.db_type,
        host=req.host,
        port=req.port,
        database_name=req.database_name,
        username=req.username,
        password=req.password,
        connect_timeout=req.connect_timeout,
    )


@router.put("/{connection_id}/rotate", summary="Rotate connection password")
def rotate_password(
    connection_id: str,
    req: RotatePasswordRequest,
    user: CurrentUser = Depends(require_permission("connections:write")),
    db: Session = Depends(get_db)
):
    """
    Update the stored encrypted password for a connection.
    Tests the new password before rotating (set test_before_rotate=false to skip).
    Running migrations that use this connection will pick up the new password
    on their next DB connection attempt.
    """
    _get_owned_or_404(db, connection_id, user)
    try:
        return manager.rotate_password(db, connection_id, req.new_password, req.test_before_rotate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{connection_id}", summary="Deactivate a connection")
def delete_connection(
    connection_id: str,
    user: CurrentUser = Depends(require_permission("connections:write")),
    db:   Session = Depends(get_db),
):
    """
    Soft-deletes a connection (sets is_active=false).
    The record is retained for audit purposes.
    Running jobs that reference this connection are not affected.
    """
    _get_owned_or_404(db, connection_id, user)
    return manager.delete(db, connection_id)
