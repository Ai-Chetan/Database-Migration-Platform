"""
Role Permission Cache
File: migration/backend/shared/middleware/role_cache.py

Caches role_definitions.permissions in memory to avoid a database round-trip
on EVERY single HTTP request (the RBAC middleware runs on every request).

Since role permissions almost never change at runtime (only when an admin
edits a policy - a rare, deliberate action), an in-memory cache refreshed
every 5 minutes is safe and dramatically reduces DB load compared to
checking on every request.

Usage:
    from backend.shared.middleware.role_cache import get_role_permissions
    perms = get_role_permissions("migration_admin")

To force an immediate refresh after an admin changes role_definitions:
    from backend.shared.middleware.role_cache import invalidate_role_cache
    invalidate_role_cache()
"""

import time
import threading
from typing import List, Dict

_cache: Dict[str, List[str]] = {}
_cache_lock = threading.Lock()
_last_loaded: float = 0.0
_CACHE_TTL_SECONDS = 300


def _load_from_db() -> Dict[str, List[str]]:
    try:
        from backend.shared.config.database import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            rows = db.execute(
                text("SELECT role_name, permissions FROM role_definitions")
            ).fetchall()
            return {row[0]: list(row[1]) if row[1] else [] for row in rows}
        finally:
            db.close()
    except Exception:
        return {
            "platform_admin": ["*"],
            "tenant_admin": [
                "manage:users", "manage:tenant_settings", "configure:policies",
                "configure:notifiers", "maintenance:mode", "emergency:stop",
                "view:audit", "create:connection", "create:job", "start:job",
                "pause:job", "resume:job", "cancel:job", "kill:worker",
                "create:masking", "create:schedule", "view:knowledge",
            ],
            "migration_admin": [
                "create:connection", "create:job", "start:job", "pause:job",
                "resume:job", "cancel:job", "kill:worker", "create:masking",
                "create:schedule", "view:knowledge", "view:audit_own",
            ],
            "migration_operator": ["start:job", "pause:job", "resume:job", "view:knowledge"],
            "read_only": ["view:jobs", "view:connections", "view:schema", "view:reports", "view:knowledge"],
            "auditor": ["view:audit", "view:reports"],
            "api_client": ["api:access"],
        }


def get_role_permissions(role_name: str) -> List[str]:
    """Returns the permission list for a role, refreshing the cache if stale."""
    global _last_loaded

    with _cache_lock:
        now = time.time()
        if not _cache or (now - _last_loaded) > _CACHE_TTL_SECONDS:
            _cache.clear()
            _cache.update(_load_from_db())
            _last_loaded = now

    return _cache.get(role_name, [])


def invalidate_role_cache() -> None:
    """Force an immediate reload on the next get_role_permissions() call."""
    global _last_loaded
    with _cache_lock:
        _last_loaded = 0.0
