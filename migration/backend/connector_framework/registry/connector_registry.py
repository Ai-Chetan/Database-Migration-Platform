"""
Connector Registry
File: migration/backend/connector_framework/registry/connector_registry.py

Central registry for all database connector plugins.
Workers and services call ConnectorRegistry.get() to get the right
connector — they never import MySQL or PostgreSQL code directly.

Built-in connectors registered at startup:
    mysql, postgresql, mariadb, sqlite

To add a new connector (e.g. Snowflake):
    1. Create connectors/snowflake/snowflake_connector.py
    2. Subclass DatabaseConnector, implement all abstract methods
    3. Call: ConnectorRegistry.register("snowflake", SnowflakeConnector)
    4. That's it — the entire platform immediately supports Snowflake

Registry also handles:
    - Capability validation (can this connector do CDC?)
    - Config validation (does config have all required fields?)
    - Version checking (is this DB version supported?)
"""

from typing import Dict, Type, List, Optional, Any
from backend.connector_framework.base.base_connector import DatabaseConnector
from backend.shared.config.logging import logger


class ConnectorRegistry:
    """
    Singleton registry of all available database connector plugins.
    Thread-safe for read operations (connectors registered at startup only).
    """

    _registry: Dict[str, Type[DatabaseConnector]] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, name: str, connector_class: Type[DatabaseConnector]) -> None:
        """Register a connector class under a name."""
        cls._registry[name.lower()] = connector_class
        logger.info("Connector registered", name=name,
                    display=getattr(connector_class, 'display_name', name))

    @classmethod
    def get(cls, db_type: str, config: Dict[str, Any]) -> DatabaseConnector:
        """
        Get an instantiated connector for the given db_type and config.
        Raises ValueError if connector not found.

        Usage:
            connector = ConnectorRegistry.get("mysql", config)
            with connector:
                schema = connector.discover_schema()
        """
        if not cls._initialized:
            cls._auto_register()

        key = db_type.lower().strip()

        # Aliases
        aliases = {
            "postgres":   "postgresql",
            "pg":         "postgresql",
            "mariadb":    "mysql",    # MariaDB is MySQL-compatible
            "aurora":     "mysql",
        }
        key = aliases.get(key, key)

        connector_class = cls._registry.get(key)
        if not connector_class:
            available = list(cls._registry.keys())
            raise ValueError(
                f"No connector registered for db_type='{db_type}'. "
                f"Available connectors: {available}"
            )

        return connector_class(config)

    @classmethod
    def get_for_config(cls, config: Dict[str, Any]) -> DatabaseConnector:
        """
        Get connector from a config dict that has an 'engine' key.
        Convenience method used throughout the worker service.
        """
        engine = config.get("engine") or config.get("db_type") or ""
        if not engine:
            raise ValueError("Config must have 'engine' or 'db_type' key")
        return cls.get(engine, config)

    @classmethod
    def list_connectors(cls) -> List[Dict[str, Any]]:
        """List all registered connectors with their capabilities."""
        if not cls._initialized:
            cls._auto_register()

        result = []
        for name, cls_ref in cls._registry.items():
            try:
                # Instantiate with empty config just to read capabilities
                tmp = cls_ref({})
                caps = tmp.capabilities
                result.append({
                    "name":         name,
                    "display_name": tmp.display_name,
                    "capabilities": {
                        "discover":    caps.discover,
                        "stream_read": caps.stream_read,
                        "bulk_write":  caps.bulk_write,
                        "cdc":         caps.cdc,
                        "checksum":    caps.checksum,
                        "constraints": caps.constraints,
                        "indexes":     caps.indexes,
                        "jsonb":       caps.jsonb,
                    }
                })
            except Exception:
                result.append({"name": name, "display_name": name, "capabilities": {}})

        return sorted(result, key=lambda x: x["name"])

    @classmethod
    def supports(cls, db_type: str, capability: str) -> bool:
        """Check if a connector supports a capability."""
        if not cls._initialized:
            cls._auto_register()
        try:
            connector = cls.get(db_type, {})
            caps = connector.capabilities
            return getattr(caps, capability, False)
        except Exception:
            return False

    @classmethod
    def validate_config(cls, db_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate that a config dict has all required fields for a connector.
        Returns {"valid": bool, "missing": [...], "warnings": [...]}
        """
        required_fields = {
            "mysql":      ["host", "port", "database", "user", "password"],
            "postgresql": ["host", "port", "database", "user", "password"],
            "sqlite":     ["database"],
        }
        key = db_type.lower()
        aliases = {"postgres": "postgresql", "pg": "postgresql", "mariadb": "mysql"}
        key = aliases.get(key, key)

        required = required_fields.get(key, ["host", "database", "user", "password"])
        missing   = [f for f in required if not config.get(f)]
        warnings  = []

        if key == "mysql" and not config.get("ssl_enabled"):
            warnings.append("SSL not enabled — recommended for production connections")
        if config.get("password", "") == "":
            warnings.append("Empty password detected")

        return {
            "valid":    len(missing) == 0,
            "missing":  missing,
            "warnings": warnings,
        }

    @classmethod
    def _auto_register(cls) -> None:
        """Register all built-in connectors on first use."""
        if cls._initialized:
            return

        try:
            from backend.connector_framework.connectors.mysql.mysql_connector import MySQLConnector
            cls.register("mysql", MySQLConnector)
        except ImportError as e:
            logger.warning("MySQL connector not available", error=str(e))

        try:
            from backend.connector_framework.connectors.postgresql.postgresql_connector import PostgreSQLConnector
            cls.register("postgresql", PostgreSQLConnector)
        except ImportError as e:
            logger.warning("PostgreSQL connector not available", error=str(e))

        try:
            from backend.connector_framework.connectors.sqlite.sqlite_connector import SQLiteConnector
            cls.register("sqlite", SQLiteConnector)
        except ImportError as e:
            logger.warning("SQLite connector not available", error=str(e))

        cls._initialized = True
        logger.info("ConnectorRegistry initialized",
                    connectors=list(cls._registry.keys()))
