"""
Plugin Manager
File: migration/backend/kernel/plugin_manager/plugin_manager.py

The universal plugin registry for the Migration Platform Kernel.

Generalizes the pattern already proven in ConnectorRegistry (connector_framework)
to ALL plugin types: connector, validator, transformer, notifier, assessment,
scheduler, policy, report, ai, storage, security, monitoring.

Design:
    Every plugin type has a base class (defined elsewhere, e.g.
    DatabaseConnector for "connector"). PluginManager doesn't care about the
    base class shape — it just stores a type→name→class mapping and
    instantiates with a config dict on request.

    This means Part 7 (Plugin Refactor) can register ValidatorPlugin,
    TransformerPlugin, NotificationProviderPlugin, PolicyPlugin subclasses
    here with zero changes to this file.

Persistence:
    Registrations made via register() are in-memory (per-process), exactly
    like ConnectorRegistry's _registry dict — this is intentional, plugins
    are Python classes and can't be "stored" in a DB.

    The plugin_registry DB TABLE is the separate, persistent CATALOG of
    what plugins exist, their capabilities, and their module_path (for
    dynamic loading) — useful for the Marketplace (Part 12) and for the
    Operations Console / Frontend to list "what's installed."

    sync_to_catalog() pushes in-memory registrations into the DB table.
    load_from_catalog() can dynamically import and register plugins whose
    module_path is stored in the DB (used for tenant-installed/marketplace
    plugins that aren't built-in Python imports at startup).

Usage:
    from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType

    # Register (usually done once at service startup)
    PluginManager.register(PluginType.VALIDATOR, "row_count", RowCountValidator)

    # Use
    validator = PluginManager.get(PluginType.VALIDATOR, "row_count", config={})

    # Introspect
    PluginManager.list_plugins(PluginType.VALIDATOR)
"""

from enum import Enum
from typing import Dict, Type, List, Optional, Any
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
import datetime
import json

from backend.shared.config.logging import logger


class PluginType(str, Enum):
    CONNECTOR    = "connector"
    VALIDATOR    = "validator"
    TRANSFORMER  = "transformer"
    NOTIFIER     = "notifier"
    ASSESSMENT   = "assessment"
    SCHEDULER    = "scheduler"
    POLICY       = "policy"
    REPORT       = "report"
    AI           = "ai"
    STORAGE      = "storage"
    SECURITY     = "security"
    MONITORING   = "monitoring"


@dataclass
class PluginInfo:
    plugin_type:  str
    name:         str
    display_name: str
    capabilities: List[str]
    version:      str = "1.0.0"
    is_builtin:   bool = True


class PluginManager:
    """
    Singleton-style registry. Class-level state, mirroring ConnectorRegistry's
    pattern exactly so existing code (ConnectorRegistry) can be refactored to
    delegate to this without behavior change.
    """

    # _registry[plugin_type][name] = class
    _registry: Dict[str, Dict[str, Type]] = {}
    _info:     Dict[str, Dict[str, PluginInfo]] = {}
    _initialized_types: set = set()

    # ── Registration ──────────────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        plugin_type:  str,
        name:         str,
        plugin_class: Type,
        display_name: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        version:      str = "1.0.0",
        is_builtin:   bool = True,
    ) -> None:
        """Register a plugin class under (plugin_type, name)."""
        ptype = plugin_type.lower()
        name  = name.lower()

        cls._registry.setdefault(ptype, {})[name] = plugin_class
        cls._info.setdefault(ptype, {})[name] = PluginInfo(
            plugin_type=ptype,
            name=name,
            display_name=display_name or getattr(plugin_class, "display_name", name),
            capabilities=capabilities or [],
            version=version,
            is_builtin=is_builtin,
        )

        logger.info(
            "Plugin registered",
            plugin_type=ptype,
            name=name,
            display_name=display_name or name,
        )

    @classmethod
    def unregister(cls, plugin_type: str, name: str) -> bool:
        """Remove a plugin registration. Returns True if it existed."""
        ptype = plugin_type.lower()
        name  = name.lower()
        existed = name in cls._registry.get(ptype, {})
        cls._registry.get(ptype, {}).pop(name, None)
        cls._info.get(ptype, {}).pop(name, None)
        return existed

    # ── Retrieval ─────────────────────────────────────────────────────────────

    @classmethod
    def get(
        cls,
        plugin_type: str,
        name:        str,
        config:      Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Get an instantiated plugin.
        Raises ValueError if not found.
        config is passed to the plugin's __init__ if provided; many plugin
        types (validators, notifiers) take no config and ignore it.
        """
        ptype = plugin_type.lower()
        key   = name.lower()

        plugin_class = cls._registry.get(ptype, {}).get(key)
        if not plugin_class:
            available = list(cls._registry.get(ptype, {}).keys())
            raise ValueError(
                f"No plugin registered for type='{ptype}', name='{name}'. "
                f"Available {ptype} plugins: {available}"
            )

        try:
            if config is not None:
                return plugin_class(config)
            return plugin_class()
        except TypeError:
            # Plugin __init__ takes no args, or takes config positionally differently
            return plugin_class()

    @classmethod
    def get_class(cls, plugin_type: str, name: str) -> Optional[Type]:
        """Get the raw class without instantiating — useful for type-checking."""
        return cls._registry.get(plugin_type.lower(), {}).get(name.lower())

    @classmethod
    def list_plugins(cls, plugin_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List registered plugins, optionally filtered by type.
        Returns plugin info dicts (not instances).
        """
        result = []
        types_to_scan = [plugin_type.lower()] if plugin_type else list(cls._info.keys())

        for ptype in types_to_scan:
            for name, info in cls._info.get(ptype, {}).items():
                result.append({
                    "plugin_type":  info.plugin_type,
                    "name":         info.name,
                    "display_name": info.display_name,
                    "version":      info.version,
                    "capabilities": info.capabilities,
                    "is_builtin":   info.is_builtin,
                })

        return sorted(result, key=lambda p: (p["plugin_type"], p["name"]))

    @classmethod
    def exists(cls, plugin_type: str, name: str) -> bool:
        return name.lower() in cls._registry.get(plugin_type.lower(), {})

    @classmethod
    def plugin_types(cls) -> List[str]:
        """List all plugin types that have at least one registration."""
        return sorted(cls._registry.keys())

    # ── DB Catalog sync (persistent, cross-process visibility) ───────────────

    @classmethod
    def sync_to_catalog(cls, db: Session, tenant_id: str = "global") -> int:
        """
        Push all currently in-memory registered plugins into the
        plugin_registry DB table. Call this once at service startup
        after registering built-in plugins, so the Frontend/Operations
        Console/Marketplace can query "what's installed" via SQL/API
        without needing every microservice's Python process alive.

        Returns count of plugins synced.
        """
        count = 0
        now = datetime.datetime.utcnow()

        for ptype, plugins in cls._info.items():
            for name, info in plugins.items():
                db.execute(
                    text("""
                        INSERT INTO plugin_registry
                            (tenant_id, plugin_type, name, display_name, version,
                             capabilities, is_active, is_builtin, created_at, updated_at)
                        VALUES
                            (:tid, :ptype, :name, :dname, :version,
                             CAST(:caps AS jsonb), TRUE, :builtin, :now, :now)
                        ON CONFLICT (tenant_id, plugin_type, name)
                        DO UPDATE SET
                            display_name = :dname,
                            version      = :version,
                            capabilities = CAST(:caps AS jsonb),
                            updated_at   = :now
                    """),
                    {
                        "tid":     tenant_id,
                        "ptype":   ptype,
                        "name":    name,
                        "dname":   info.display_name,
                        "version": info.version,
                        "caps":    json.dumps(info.capabilities),
                        "builtin": info.is_builtin,
                        "now":     now,
                    }
                )
                count += 1

        db.commit()
        logger.info("Plugin catalog synced", tenant_id=tenant_id, count=count)
        return count

    @classmethod
    def query_catalog(
        cls,
        db:          Session,
        plugin_type: Optional[str] = None,
        tenant_id:   Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Query the persistent plugin_registry table — works across processes,
        doesn't require the plugin's Python class to be importable right now.
        Used by Frontend / Marketplace / Operations Console.
        """
        conditions = []
        params: Dict[str, Any] = {}

        if plugin_type:
            conditions.append("plugin_type = :ptype")
            params["ptype"] = plugin_type.lower()
        if tenant_id:
            conditions.append("(tenant_id = :tid OR tenant_id = 'global')")
            params["tid"] = tenant_id
        if active_only:
            conditions.append("is_active = TRUE")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        rows = db.execute(
            text(f"""
                SELECT plugin_type, name, display_name, version, capabilities,
                       is_active, is_builtin, created_at, updated_at
                FROM plugin_registry
                {where}
                ORDER BY plugin_type, name
            """),
            params
        ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            result.append(d)
        return result

    @classmethod
    def reset(cls) -> None:
        """Clear all in-memory registrations. Mainly for tests."""
        cls._registry.clear()
        cls._info.clear()
        cls._initialized_types.clear()
