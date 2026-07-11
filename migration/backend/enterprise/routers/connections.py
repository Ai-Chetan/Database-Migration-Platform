"""
Connection Manager Router
File: migration/backend/enterprise/routers/connections.py

Endpoints:
    POST /connections              → register new connection
    GET  /connections              → list all connections (no passwords)
    GET  /connections/{id}         → get one connection
    POST /connections/{id}/test    → test an existing connection
    POST /connections/test-raw     → test without saving
    PUT  /connections/{id}/rotate  → rotate password
    DELETE /connections/{id}       → deactivate connection
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.enterprise.connection_manager.connection_manager import ConnectionManager

router  = APIRouter(prefix="/connections", tags=["Connection Manager"])
manager = ConnectionManager()


class RegisterConnectionRequest(BaseModel):
    tenant_id:        str = "local"
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


@router.post("", summary="Register a new database connection")
def register_connection(req: RegisterConnectionRequest, db: Session = Depends(get_db)):
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
            tenant_id=req.tenant_id,
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
def list_connections(tenant_id: str = "local", db: Session = Depends(get_db)):
    """
    Returns all active connections for the tenant.
    Passwords are NEVER returned. Use /test to verify a connection is alive.
    """
    return manager.list(db, tenant_id)


@router.get("/{connection_id}", summary="Get one connection record")
def get_connection(connection_id: str, db: Session = Depends(get_db)):
    conn = manager.get(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")
    return conn


@router.post("/{connection_id}/test", summary="Test an existing registered connection")
def test_connection(connection_id: str, db: Session = Depends(get_db)):
    """
    Decrypts the stored password and attempts a live connection.
    Updates last_tested_at and last_test_status in the registry.

    Returns:
      - success: true/false
      - db_version: database version string
      - latency_ms: round-trip time in milliseconds
      - error: error message if failed
    """
    conn = manager.get(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")
    return manager.test(db, connection_id)


@router.post("/test-raw", summary="Test a connection without saving it")
def test_raw_connection(req: TestRawRequest):
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
    db: Session = Depends(get_db)
):
    """
    Update the stored encrypted password for a connection.
    Tests the new password before rotating (set test_before_rotate=false to skip).
    Running migrations that use this connection will pick up the new password
    on their next DB connection attempt.
    """
    conn = manager.get(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")
    try:
        return manager.rotate_password(db, connection_id, req.new_password, req.test_before_rotate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{connection_id}", summary="Deactivate a connection")
def delete_connection(connection_id: str, db: Session = Depends(get_db)):
    """
    Soft-deletes a connection (sets is_active=false).
    The record is retained for audit purposes.
    Running jobs that reference this connection are not affected.
    """
    conn = manager.get(db, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Connection {connection_id} not found")
    return manager.delete(db, connection_id)
