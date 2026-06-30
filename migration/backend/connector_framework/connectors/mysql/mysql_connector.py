"""
MySQL Connector
File: migration/backend/connector_framework/connectors/mysql/mysql_connector.py

Full implementation of DatabaseConnector for MySQL 5.6, 5.7, 8.x and MariaDB.
Supports all capabilities including CDC via binlog reading.
"""

import time
import datetime
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult, CDCPosition
)
from backend.shared.config.logging import logger


class MySQLConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "mysql"

    @property
    def display_name(self) -> str:
        return "MySQL"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=True, checksum=True, constraints=True, indexes=True,
            jsonb=True, partitioning=True,
        )

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import mysql.connector
        self._connection = mysql.connector.connect(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 3306)),
            database=self.config.get("database"),
            user=self.config.get("user"),
            password=self.config.get("password"),
            autocommit=False,
            connection_timeout=int(self.config.get("connect_timeout", 30)),
            ssl_disabled=not self.config.get("ssl_enabled", False),
        )
        logger.info("MySQL connected", host=self.config.get("host"), db=self.config.get("database"))

    def disconnect(self) -> None:
        if self._connection:
            try:
                if self._connection.is_connected():
                    self._connection.close()
            except Exception:
                pass
            self._connection = None

    def _conn(self):
        if not self._connection or not self._connection.is_connected():
            self.connect()
        return self._connection

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 3306)),
                database=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                connection_timeout=10,
            )
            cur = conn.cursor()
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.close()
            conn.close()
            return {
                "success": True, "db_version": version,
                "latency_ms": int((time.time() - start) * 1000),
                "error": None,
            }
        except Exception as e:
            return {"success": False, "db_version": None,
                    "latency_ms": int((time.time() - start) * 1000), "error": str(e)}

    # ── Schema discovery ──────────────────────────────────────────────────────

    def discover_schema(self) -> SchemaInfo:
        conn    = self._conn()
        cursor  = conn.cursor(dictionary=True)
        db_name = self.config.get("database")
        tables  = {}

        cursor.execute(
            "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
            (db_name,)
        )
        for row in cursor.fetchall():
            tname = row["TABLE_NAME"]
            tables[tname] = {
                "columns": {}, "primary_keys": [],
                "foreign_keys": [], "indexes": [],
                "row_count": int(row.get("TABLE_ROWS") or 0)
            }

        for tname in tables:
            # Columns
            cursor.execute(
                "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY,COLUMN_DEFAULT,EXTRA "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                "ORDER BY ORDINAL_POSITION", (db_name, tname)
            )
            for col in cursor.fetchall():
                tables[tname]["columns"][col["COLUMN_NAME"]] = {
                    "type": col["COLUMN_TYPE"], "nullable": col["IS_NULLABLE"]=="YES",
                    "pk": col["COLUMN_KEY"]=="PRI", "unique": col["COLUMN_KEY"]=="UNI",
                    "default": col["COLUMN_DEFAULT"], "extra": col["EXTRA"] or "",
                }
                if col["COLUMN_KEY"] == "PRI":
                    tables[tname]["primary_keys"].append(col["COLUMN_NAME"])

            # FKs
            cursor.execute(
                "SELECT COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME,CONSTRAINT_NAME "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
                (db_name, tname)
            )
            for fk in cursor.fetchall():
                tables[tname]["foreign_keys"].append({
                    "column": fk["COLUMN_NAME"], "ref_table": fk["REFERENCED_TABLE_NAME"],
                    "ref_column": fk["REFERENCED_COLUMN_NAME"],
                    "constraint_name": fk["CONSTRAINT_NAME"],
                })

            # Indexes
            cursor.execute(
                "SELECT INDEX_NAME, "
                "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') AS cols, "
                "NON_UNIQUE FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s GROUP BY INDEX_NAME,NON_UNIQUE",
                (db_name, tname)
            )
            for idx in cursor.fetchall():
                tables[tname]["indexes"].append({
                    "name": idx["INDEX_NAME"],
                    "columns": idx["cols"].split(",") if idx["cols"] else [],
                    "unique": idx["NON_UNIQUE"] == 0,
                })

        cursor.close()
        return SchemaInfo(database=db_name, engine="mysql", tables=tables)

    def get_row_count(self, table_name: str) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                "SELECT TABLE_ROWS FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (self.config.get("database"), table_name)
            )
            row = cursor.fetchone()
            if row and row[0] and int(row[0]) > 1000:
                return int(row[0])
            cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def get_avg_row_size(self, table_name: str) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                "SELECT AVG_ROW_LENGTH FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (self.config.get("database"), table_name)
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] else 512
        finally:
            cursor.close()

    # ── Data movement ─────────────────────────────────────────────────────────

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=5000
    ) -> Generator[Dict[str, Any], None, None]:
        import mysql.connector
        conn   = mysql.connector.connect(**self._raw_config())
        cursor = conn.cursor(dictionary=True, buffered=False)
        cols   = "`" + "`, `".join(columns) + "`" if columns else "*"
        try:
            cursor.execute(
                f"SELECT {cols} FROM `{table_name}` "
                f"WHERE `{pk_column}` BETWEEN %s AND %s ORDER BY `{pk_column}`",
                (pk_start, pk_end)
            )
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    yield row
        finally:
            cursor.close()
            conn.close()

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)

        start   = time.time()
        conn    = self._conn()
        cursor  = conn.cursor()
        columns = list(rows[0].keys())
        col_str = ", ".join(f"`{c}`" for c in columns)
        ph_str  = ", ".join(["%s"] * len(columns))
        values  = [tuple(r[c] for c in columns) for r in rows]

        keyword = "INSERT IGNORE" if mode == "ignore_duplicates" else "INSERT"
        sql     = f"{keyword} INTO `{table_name}` ({col_str}) VALUES ({ph_str})"

        try:
            cursor.executemany(sql, values)
            conn.commit()
            inserted = cursor.rowcount
            elapsed  = int((time.time() - start) * 1000)
            cursor.close()
            return BulkWriteResult(
                rows_inserted=len(rows) if mode == "ignore_duplicates" else inserted,
                rows_skipped=0, rows_failed=0, duration_ms=elapsed
            )
        except Exception as e:
            conn.rollback()
            cursor.close()
            return BulkWriteResult(0, 0, len(rows), int((time.time()-start)*1000), str(e))

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM `{table_name}` "
                f"WHERE `{pk_column}` BETWEEN %s AND %s", (pk_start, pk_end)
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        cursor = self._conn().cursor()
        try:
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
            cols   = [d[0] for d in cursor.description]
            concat = ", '|', ".join(f"IFNULL(CAST(`{c}` AS CHAR),'NULL')" for c in cols)
            cursor.execute(
                f"SELECT MD5(CAST(SUM(CONV(SUBSTRING(MD5(CONCAT({concat})),1,8),16,10)) AS CHAR)) "
                f"FROM `{table_name}` WHERE `{pk_column}` BETWEEN %s AND %s",
                (pk_start, pk_end)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else "empty"
        finally:
            cursor.close()

    # ── DDL ───────────────────────────────────────────────────────────────────

    def truncate_table(self, table_name: str) -> None:
        cursor = self._conn().cursor()
        try:
            cursor.execute(f"TRUNCATE TABLE `{table_name}`")
            self._conn().commit()
        finally:
            cursor.close()

    def execute_ddl(self, ddl: str) -> None:
        cursor = self._conn().cursor()
        try:
            for statement in ddl.split(";"):
                s = statement.strip()
                if s:
                    cursor.execute(s)
            self._conn().commit()
        finally:
            cursor.close()

    # ── CDC via binlog ────────────────────────────────────────────────────────

    def get_cdc_position(self) -> CDCPosition:
        cursor = self._conn().cursor()
        try:
            cursor.execute("SHOW MASTER STATUS")
            row = cursor.fetchone()
            if not row:
                raise ValueError("Binary logging is not enabled on this MySQL server. "
                                 "Set binlog_format=ROW and log_bin=ON.")
            return CDCPosition(method="binlog", file=row[0], position=int(row[1]))
        finally:
            cursor.close()

    def start_cdc_capture(self, tables, position, callback) -> None:
        """
        Start MySQL binlog CDC capture using mysql-replication library.
        Calls callback(event_type, table, before_image, after_image, position)
        for each INSERT/UPDATE/DELETE event.

        Requires: pip install mysql-replication
        Requires MySQL server: binlog_format=ROW, log_bin=ON
        """
        try:
            from pymysqlreplication import BinLogStreamReader
            from pymysqlreplication.row_event import (
                DeleteRowsEvent, UpdateRowsEvent, WriteRowsEvent
            )
        except ImportError:
            raise ImportError(
                "mysql-replication package required for CDC. "
                "Install with: pip install mysql-replication"
            )

        self._cdc_running = True
        stream = BinLogStreamReader(
            connection_settings={
                "host":   self.config.get("host", "localhost"),
                "port":   int(self.config.get("port", 3306)),
                "user":   self.config.get("user"),
                "passwd": self.config.get("password"),
            },
            server_id=int(self.config.get("server_id", 100)),
            log_file=position.file,
            log_pos=position.position,
            only_schemas=[self.config.get("database")],
            only_tables=tables if tables else None,
            blocking=True,
            resume_stream=True,
        )

        try:
            for binlog_event in stream:
                if not self._cdc_running:
                    break

                if isinstance(binlog_event, WriteRowsEvent):
                    for row in binlog_event.rows:
                        callback(
                            "INSERT", binlog_event.table,
                            None, row["values"],
                            CDCPosition("binlog", binlog_event.log_file,
                                        binlog_event.log_pos)
                        )
                elif isinstance(binlog_event, UpdateRowsEvent):
                    for row in binlog_event.rows:
                        callback(
                            "UPDATE", binlog_event.table,
                            row["before_values"], row["after_values"],
                            CDCPosition("binlog", binlog_event.log_file,
                                        binlog_event.log_pos)
                        )
                elif isinstance(binlog_event, DeleteRowsEvent):
                    for row in binlog_event.rows:
                        callback(
                            "DELETE", binlog_event.table,
                            row["values"], None,
                            CDCPosition("binlog", binlog_event.log_file,
                                        binlog_event.log_pos)
                        )
        finally:
            stream.close()

    def stop_cdc_capture(self) -> None:
        self._cdc_running = False

    # ── Helper ────────────────────────────────────────────────────────────────

    def _raw_config(self) -> dict:
        return {
            "host":               self.config.get("host", "localhost"),
            "port":               int(self.config.get("port", 3306)),
            "database":           self.config.get("database"),
            "user":               self.config.get("user"),
            "password":           self.config.get("password"),
            "connection_timeout": 30,
        }
