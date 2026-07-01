"""
Plugin Manager Router
File: migration/backend/kernel/routers/plugins.py

Endpoints:
    GET  /plugins                      → list all in-memory registered plugins
    GET  /plugins/{type}               → list plugins of one type
    POST /plugins/sync                 → push in-memory registrations to DB catalog
    GET  /plugins/catalog              → query the persistent plugin_registry table
    GET  /plugins/catalog/{type}       → query catalog filtered by type
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from backend.shared.config.database import get_db
from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType

router = APIRouter(prefix="/plugins", tags=["Plugin Manager"])


@router.get("", summary="List all currently registered plugins (in-memory)")
def list_plugins():
    """
    Lists every plugin registered in THIS process via PluginManager.register().
    Each microservice process registers its own plugins at startup (connectors,
    validators, transformers, etc.) — this reflects what's live right now.

    For a cross-process, persistent view, use GET /plugins/catalog instead.
    """
    return PluginManager.list_plugins()


@router.get("/{plugin_type}", summary="List plugins of one type")
def list_plugins_by_type(plugin_type: str):
    """
    Example: GET /plugins/connector → lists mysql, postgresql, sqlite (once
    those services have registered with this process — typically each
    microservice's own plugin manager state, not shared across services
    unless synced via /plugins/sync + /plugins/catalog).
    """
    valid_types = [t.value for t in PluginType]
    if plugin_type.lower() not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_type '{plugin_type}'. Must be one of: {valid_types}"
        )
    return PluginManager.list_plugins(plugin_type)


@router.post("/sync", summary="Sync in-memory plugin registrations to persistent catalog")
def sync_catalog(tenant_id: str = "global", db: Session = Depends(get_db)):
    """
    Push everything registered in THIS process into the plugin_registry DB
    table, making it visible to other processes, the Frontend, and the
    Marketplace (Part 12).

    Call this once at service startup, after all built-in plugins for that
    service have called PluginManager.register().
    """
    count = PluginManager.sync_to_catalog(db, tenant_id)
    return {"synced": count, "tenant_id": tenant_id}


@router.get("/catalog/all", summary="Query the persistent plugin catalog")
def query_catalog(
    plugin_type: Optional[str] = None,
    tenant_id:   Optional[str] = "global",
    active_only: bool = True,
    db:          Session = Depends(get_db),
):
    """
    Query the DURABLE plugin catalog — works regardless of which process
    you're calling, doesn't require the plugin's Python class to be
    importable in this process right now.

    This is what the Frontend's "Installed Plugins" page and the
    Marketplace browser (Part 12) will use.
    """
    return PluginManager.query_catalog(db, plugin_type=plugin_type,
                                       tenant_id=tenant_id, active_only=active_only)


@router.get("/catalog/{plugin_type}", summary="Query catalog filtered by type")
def query_catalog_by_type(
    plugin_type: str,
    tenant_id:   Optional[str] = "global",
    db:          Session = Depends(get_db),
):
    return PluginManager.query_catalog(db, plugin_type=plugin_type, tenant_id=tenant_id)
