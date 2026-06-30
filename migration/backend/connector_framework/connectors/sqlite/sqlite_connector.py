"""
SQLite Connector
File: migration/backend/connector_framework/connectors/sqlite/sqlite_connector.py

SQLite connector — useful for local development and testing migrations
without needing a running MySQL/PostgreSQL server.
Does NOT support CDC (SQLite has no binlog/WAL replication).
"""

import time
import sqlite3
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult, CDCPosition
)
from backend.shared.config.logging import logger


class SQLiteConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "sqlite"

    @property
    def display_name(self) -> str:
        return "SQLite"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=False, checksum=True, constraints=True, indexes=True,
            jsonb=False, partitioning=False,
        )

    def connect(self) -> None:
        db_path = self.config.get("database", ":memory:")
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        logger.info("SQLite connected", path=db_path)

    def disconnect(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None

    def _conn(self):
        if not self._connection:
            self.connect()
        return self._connection

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            db_path = self.config.get("database", ":memory:")
            conn    = sqlite3.connect(db_path, timeout=5)
            cur     = conn.cursor()
            cur.execute("SELECT sqlite_version()")
            version = cur.fetchone()[0]
            conn.close()
            return {"success": True, "db_version": f"SQLite {version}",
                    "latency_ms": int((time.time()-start)*1000), "error": None}
        except Exception as e:
            return {"success": False, "db_version": None,
                    "latency_ms": int((time.time()-start)*1000), "error": str(e)}

    def discover_schema(self) -> SchemaInfo:
        conn   = self._conn()
        cursor = conn.cursor()
        tables = {}

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        for row in cursor.fetchall():
            tname = row[0]
            tables[tname] = {"columns": {}, "primary_keys": [],
                             "foreign_keys": [], "indexes": [], "row_count": 0}

            cursor.execute(f"PRAGMA table_info({tname})")
            for col in cursor.fetchall():
                col = dict(col)
                tables[tname]["columns"][col["name"]] = {
                    "type":     col["type"],
                    "nullable": col["notnull"] == 0,
                    "pk":       col["pk"] > 0,
                    "unique":   False,
                    "default":  col["dflt_value"],
                    "extra":    "",
                }
                if col["pk"] > 0:
                    tables[tname]["primary_keys"].append(col["name"])

            cursor.execute(f"PRAGMA foreign_key_list({tname})")
            for fk in cursor.fetchall():
                fk = dict(fk)
                tables[tname]["foreign_keys"].append({
                    "column": fk["from"], "ref_table": fk["table"],
                    "ref_column": fk["to"], "constraint_name": f"fk_{tname}_{fk['from']}",
                })

            cursor.execute(f"PRAGMA index_list({tname})")
            for idx in cursor.fetchall():
                idx = dict(idx)
                tables[tname]["indexes"].append({
                    "name": idx["name"], "unique": bool(idx["unique"]), "columns": [],
                })

            cursor.execute(f"SELECT COUNT(*) FROM {tname}")
            tables[tname]["row_count"] = cursor.fetchone()[0]

        return SchemaInfo(database=self.config.get("database", ":memory:"),
                         engine="sqlite", tables=tables)

    def get_row_count(self, table_name: str) -> int:
        cursor = self._conn().cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cursor.fetchone()[0]

    def get_avg_row_size(self, table_name: str) -> int:
        return 256   # SQLite doesn't expose row sizes easily

    def stream_rows(self, table_name, pk_column, pk_start, pk_end,
                    columns=None, batch_size=5000) -> Generator[Dict[str, Any], None, None]:
        conn   = self._conn()
        cursor = conn.cursor()
        cols   = ", ".join(columns) if columns else "*"
        cursor.execute(
            f"SELECT {cols} FROM {table_name} "
            f"WHERE {pk_column} BETWEEN ? AND ? ORDER BY {pk_column}",
            (pk_start, pk_end)
        )
        col_names = [d[0] for d in cursor.description]
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            for row in batch:
                yield dict(zip(col_names, row))

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)
        start   = time.time()
        conn    = self._conn()
        columns = list(rows[0].keys())
        col_str = ", ".join(columns)
        ph_str  = ", ".join(["?"] * len(columns))
        keyword = "INSERT OR IGNORE" if mode == "ignore_duplicates" else "INSERT"
        sql     = f"{keyword} INTO {table_name} ({col_str}) VALUES ({ph_str})"
        values  = [tuple(r[c] for c in columns) for r in rows]
        try:
            conn.executemany(sql, values)
            conn.commit()
            return BulkWriteResult(len(rows), 0, 0, int((time.time()-start)*1000))
        except Exception as e:
            conn.rollback()
            return BulkWriteResult(0, 0, len(rows), int((time.time()-start)*1000), str(e))

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        cursor = self._conn().cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE {pk_column} BETWEEN ? AND ?",
            (pk_start, pk_end)
        )
        return cursor.fetchone()[0]

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        import hashlib
        cursor = self._conn().cursor()
        cursor.execute(
            f"SELECT * FROM {table_name} WHERE {pk_column} BETWEEN ? AND ? ORDER BY {pk_column}",
            (pk_start, pk_end)
        )
        rows = cursor.fetchall()
        combined = "|".join(str(r) for r in rows)
        return hashlib.md5(combined.encode()).hexdigest()

    def truncate_table(self, table_name: str) -> None:
        self._conn().execute(f"DELETE FROM {table_name}")
        self._conn().commit()

    def execute_ddl(self, ddl: str) -> None:
        conn = self._conn()
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()
