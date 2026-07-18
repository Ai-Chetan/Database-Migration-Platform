"""
Tenant Scoping Helper
File: migration/backend/shared/tenant/tenant_scope.py

Provides a consistent way to enforce tenant isolation across every router
in the platform. Without this, a user's tenant_id (from their JWT) is never
checked against the resources they're querying — meaning any authenticated
user could potentially view/modify another tenant's connections, jobs,
reports, etc. by guessing or enumerating UUIDs.

Two usage patterns:

1. SQL WHERE-clause injection (for raw `text()` queries — most of this codebase):

    from backend.shared.tenant.tenant_scope import tenant_filter

    where_sql, params = tenant_filter(user, table_alias="mj")
    rows = db.execute(
        text(f"SELECT * FROM migration_jobs mj WHERE {where_sql}"),
        params
    ).fetchall()

2. Ownership verification (for single-resource GET/PUT/DELETE by ID):

    from backend.shared.tenant.tenant_scope import assert_owned_by_tenant

    row = db.execute(text("SELECT * FROM connections WHERE id=:id"), {"id": conn_id}).fetchone()
    assert_owned_by_tenant(row, user)   # raises 404 if tenant_id doesn't match

Design principle: a resource belonging to another tenant should return 404,
NOT 403. Returning 403 confirms the resource exists (information leak);
404 does not.

platform_admin bypasses all tenant checks (superuser, sees everything —
needed for platform-wide operations like emergency stop across tenants).
"""

from typing import Dict, Any, Tuple, Optional
from fastapi import HTTPException


def is_superuser(user: Dict[str, Any]) -> bool:
    """platform_admin sees all tenants. Every other role is tenant-scoped."""
    return user.get("role") == "platform_admin"


def tenant_filter(
    user:         Dict[str, Any],
    table_alias:  str = "",
    column:       str = "tenant_id",
) -> Tuple[str, Dict[str, Any]]:
    """
    Returns a (where_clause_fragment, params_dict) pair to inject into a
    raw SQL WHERE clause. If the user is platform_admin, returns "1=1"
    (no filtering) so admins retain platform-wide visibility.

    Example:
        where_sql, params = tenant_filter(user, table_alias="mj")
        # where_sql = "mj.tenant_id = :tenant_scope"
        # params    = {"tenant_scope": "acme_corp"}

    Merge params into your existing query params dict before executing.
    """
    if is_superuser(user):
        return "1=1", {}

    prefix = f"{table_alias}." if table_alias else ""
    return f"{prefix}{column} = :tenant_scope", {"tenant_scope": user.get("tenant_id", "local")}


def assert_owned_by_tenant(
    row:     Any,
    user:    Dict[str, Any],
    column:  str = "tenant_id",
    resource_name: str = "Resource",
) -> None:
    """
    Verifies a fetched row belongs to the caller's tenant.
    Raises 404 (not 403 — avoid leaking existence) if it does not.
    Call this immediately after fetching any single resource by ID,
    before returning it or allowing mutation.

    row can be a SQLAlchemy Row (supports both row.tenant_id and row['tenant_id'])
    or a plain dict.
    """
    if row is None:
        raise HTTPException(status_code=404, detail=f"{resource_name} not found.")

    if is_superuser(user):
        return

    if hasattr(row, "_mapping"):
        row_tenant = row._mapping.get(column)
    elif isinstance(row, dict):
        row_tenant = row.get(column)
    else:
        row_tenant = getattr(row, column, None)

    if row_tenant != user.get("tenant_id"):
        raise HTTPException(status_code=404, detail=f"{resource_name} not found.")


def scoped_params(user: Dict[str, Any], **extra_params) -> Dict[str, Any]:
    """
    Convenience helper: returns a params dict with tenant_scope pre-filled,
    merged with any extra params you pass. Use alongside tenant_filter()
    when you want one call instead of two.

    Example:
        where_sql, _ = tenant_filter(user, table_alias="c")
        params = scoped_params(user, conn_id=connection_id)
        db.execute(text(f"SELECT * FROM connections c WHERE {where_sql} AND c.id=:conn_id"), params)
    """
    params = {"tenant_scope": user.get("tenant_id", "local")}
    params.update(extra_params)
    return params
