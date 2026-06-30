"""
Database Connector Base Class — Plugin Interface
File: migration/backend/connector_framework/base/base_connector.py

This is the heart of the Plugin Architecture.

Every database connector (MySQL, PostgreSQL, Oracle, Snowflake, BigQuery...)
implements this single interface. The worker engine only talks to this
abstract interface — it never imports MySQL or PostgreSQL code directly.

Adding a new database requires implementing 8 methods here.
Nothing in the core engine changes.

Capabilities a connector can declare:
  discover         → can read schema via INFORMATION_SCHEMA or equivalent
  stream_read      → can stream rows with low memory footprint
  bulk_write       → can perform high-throughput bulk inserts
  cdc              → supports Change Data Capture
  checksum         → can compute row-level checksums natively in SQL
  constraints      → can extract and generate PK/FK/UNIQUE/CHECK DDL
  indexes          → can extract and generate CREATE INDEX DDL
  jsonb            → native JSON/JSONB column support
  partitioning     → supports table partitioning

Usage:
    from backend.connector_framework.registry.connector_registry import ConnectorRegistry

    # Get the right connector for a config
    connector = ConnectorRegistry.get("mysql", config)

    # Use the unified interface
    schema = connector.discover_schema()
    for row in connector.stream_rows("users", pk_col="id", start=1, end=100000):
        ...
    connector.bulk_insert("users", rows)
    connector.close()
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Generator, Optional
from dataclasses import dataclass, field


@dataclass
class ConnectorCapabilities:
    """Declares what a connector can do."""
    discover:      bool = True
    stream_read:   bool = True
    bulk_write:    bool = True
    cdc:           bool = False
    checksum:      bool = False
    constraints:   bool = True
    indexes:       bool = True
    jsonb:         bool = False
    partitioning:  bool = False


@dataclass
class SchemaInfo:
    """Unified schema representation returned by discover_schema()."""
    database:   str
    engine:     str
    tables:     Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Same format as SchemaDiscovery.discover() in schema_mapping_service


@dataclass
class BulkWriteResult:
    rows_inserted:   int
    rows_skipped:    int  # duplicates ignored
    rows_failed:     int
    duration_ms:     int
    error_message:   Optional[str] = None


@dataclass
class CDCPosition:
    """Represents a position in the source DB change stream."""
    method:     str   # binlog | wal | timestamp
    file:       Optional[str]  = None   # MySQL binlog file
    position:   Optional[int]  = None   # MySQL binlog position
    lsn:        Optional[str]  = None   # PostgreSQL WAL LSN
    timestamp:  Optional[str]  = None   # Timestamp-based fallback


class DatabaseConnector(ABC):
    """
    Abstract base class for all database connectors.

    To add a new database (e.g. Snowflake):
      1. Create backend/connector_framework/connectors/snowflake/snowflake_connector.py
      2. Subclass DatabaseConnector
      3. Implement all @abstractmethod methods
      4. Register: ConnectorRegistry.register("snowflake", SnowflakeConnector)

    That's all. The worker engine, schema service, and CDC engine
    all use this interface — they don't know or care about Snowflake specifics.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        config: connection parameters dict
          {engine, host, port, database, user, password, ...}
        """
        self.config = config
        self._connection = None

    # ── Required: Identity ────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Connector identifier e.g. 'mysql', 'postgresql', 'snowflake'"""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name e.g. 'MySQL 8.0', 'PostgreSQL 15'"""

    @property
    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """What this connector can do."""

    # ── Required: Connection lifecycle ────────────────────────────────────────

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the database."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close all connections cleanly."""

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the connection and return info.
        Returns: {"success": bool, "db_version": str, "latency_ms": int, "error": str|None}
        """

    # ── Required: Schema discovery ────────────────────────────────────────────

    @abstractmethod
    def discover_schema(self) -> SchemaInfo:
        """
        Read the full schema of the connected database.
        Returns SchemaInfo with tables, columns, PKs, FKs, indexes.
        """

    @abstractmethod
    def get_row_count(self, table_name: str) -> int:
        """Return approximate row count for a table."""

    @abstractmethod
    def get_avg_row_size(self, table_name: str) -> int:
        """Return average row size in bytes for chunk planning."""

    # ── Required: Data movement ───────────────────────────────────────────────

    @abstractmethod
    def stream_rows(
        self,
        table_name: str,
        pk_column:  str,
        pk_start:   Any,
        pk_end:     Any,
        columns:    Optional[List[str]] = None,
        batch_size: int = 5000,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream rows from a table within a PK range.
        Must be memory-safe — never loads entire table at once.
        Yields dicts: {"col1": val1, "col2": val2, ...}
        """

    @abstractmethod
    def bulk_insert(
        self,
        table_name: str,
        rows:       List[Dict[str, Any]],
        mode:       str = "ignore_duplicates",  # ignore_duplicates | upsert | fail_on_duplicate
    ) -> BulkWriteResult:
        """
        Insert rows into a table in bulk.
        mode=ignore_duplicates → INSERT IGNORE / ON CONFLICT DO NOTHING (idempotent)
        mode=upsert            → INSERT ... ON DUPLICATE KEY UPDATE / ON CONFLICT DO UPDATE
        mode=fail_on_duplicate → plain INSERT (fails if duplicate)
        """

    @abstractmethod
    def count_rows_in_range(
        self,
        table_name: str,
        pk_column:  str,
        pk_start:   Any,
        pk_end:     Any,
    ) -> int:
        """Count rows in a PK range. Used for validation."""

    @abstractmethod
    def compute_checksum(
        self,
        table_name: str,
        pk_column:  str,
        pk_start:   Any,
        pk_end:     Any,
    ) -> str:
        """
        Compute a deterministic checksum of all row data in a PK range.
        Used for data integrity validation after migration.
        Returns a hex string (MD5 or similar).
        """

    # ── Optional: DDL operations ──────────────────────────────────────────────

    def generate_create_table_ddl(
        self,
        table_name:     str,
        schema_info:    Dict[str, Any],
        target_db_type: str = None,
    ) -> str:
        """
        Generate CREATE TABLE DDL for the given table.
        Optional — connectors that don't support DDL generation can raise NotImplementedError.
        """
        raise NotImplementedError(f"{self.name} does not support DDL generation")

    def execute_ddl(self, ddl: str) -> None:
        """Execute a DDL statement (CREATE TABLE, ALTER TABLE, etc.)"""
        raise NotImplementedError(f"{self.name} does not support DDL execution")

    def truncate_table(self, table_name: str) -> None:
        """Truncate a table. Used by rollback engine."""
        raise NotImplementedError(f"{self.name} does not support TRUNCATE")

    # ── Optional: CDC ─────────────────────────────────────────────────────────

    def get_cdc_position(self) -> CDCPosition:
        """Return the current change stream position. Required if capabilities.cdc=True."""
        raise NotImplementedError(f"{self.name} does not support CDC")

    def start_cdc_capture(
        self,
        tables:   List[str],
        position: CDCPosition,
        callback: callable,
    ) -> None:
        """
        Start capturing changes from the source DB.
        Calls callback(event_type, table, before_image, after_image, position)
        for each captured change.
        Required if capabilities.cdc=True.
        """
        raise NotImplementedError(f"{self.name} does not support CDC")

    def stop_cdc_capture(self) -> None:
        """Stop the CDC capture stream."""
        raise NotImplementedError(f"{self.name} does not support CDC")

    # ── Context manager support ───────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def close(self):
        """Alias for disconnect() — used by existing worker code."""
        self.disconnect()
