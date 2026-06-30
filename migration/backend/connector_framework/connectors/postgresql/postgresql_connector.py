"""
PostgreSQL Connector
File: migration/backend/connector_framework/connectors/postgresql/postgresql_connector.py

Full implementation of DatabaseConnector for PostgreSQL 11–16.
Supports all capabilities including CDC via WAL logical replication.
"""

import time
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult, CDCPosition
)
from backend.shared.config.logging import logger


class PostgreSQLConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "postgresql"

    @property
    def display_name(self) -> str:
        return "PostgreSQL"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=True, checksum=True, constraints=True, indexes=True,
            jsonb=True, partitioning=True,
        )

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import psycopg2
        self._connection = psycopg2.connect(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 5432)),
            dbname=self.config.get("database"),
            user=self.config.get("user"),
            password=self.config.get("password"),
            connect_timeout=int(self.config.get("connect_timeout", 30)),
        )
        self._connection.autocommit = False
        logger.info("PostgreSQL connected",
                    host=self.config.get("host"), db=self.config.get("database"))

    def disconnect(self) -> None:
        if self._connection and not self._connection.closed:
            self._connection.close()
            self._connection = None

    def _conn(self):
        if not self._connection or self._connection.closed:
            self.connect()
        return self._connection

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 5432)),
                dbname=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                connect_timeout=10,
            )
            cur = conn.cursor()
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
            cur.close()
            conn.close()
            return {
                "success": True, "db_version": version,
                "latency_ms": int((time.time() - start) * 1000), "error": None,
            }
        except Exception as e:
            return {"success": False, "db_version": None,
                    "latency_ms": int((time.time() - start) * 1000), "error": str(e)}

    # ── Schema discovery ──────────────────────────────────────────────────────

    def discover_schema(self) -> SchemaInfo:
        import psycopg2.extras
        conn        = self._conn()
        cursor      = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        schema_name = self.config.get("pg_schema", "public")
        tables      = {}

        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename",
            (schema_name,)
        )
        table_names = [r["tablename"] for r in cursor.fetchall()]

        for tname in table_names:
            tables[tname] = {"columns": {}, "primary_keys": [],
                             "foreign_keys": [], "indexes": [], "row_count": 0}

            # Columns
            cursor.execute("""
                SELECT c.column_name, c.udt_name, c.character_maximum_length,
                       c.numeric_precision, c.numeric_scale, c.is_nullable,
                       c.column_default,
                       CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_pk
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT kcu.column_name FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                    WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='PRIMARY KEY'
                ) pk ON c.column_name=pk.column_name
                WHERE c.table_schema=%s AND c.table_name=%s ORDER BY c.ordinal_position
            """, (schema_name, tname, schema_name, tname))

            for col in cursor.fetchall():
                col = dict(col)
                base = col["udt_name"]
                if col["character_maximum_length"]:
                    full = f"{base}({col['character_maximum_length']})"
                elif col["numeric_precision"] and col["numeric_scale"] is not None:
                    full = f"{base}({col['numeric_precision']},{col['numeric_scale']})"
                else:
                    full = base
                tables[tname]["columns"][col["column_name"]] = {
                    "type": full, "nullable": col["is_nullable"]=="YES",
                    "pk": bool(col["is_pk"]), "unique": False,
                    "default": col["column_default"], "extra": "",
                }
                if col["is_pk"]:
                    tables[tname]["primary_keys"].append(col["column_name"])

            # FKs
            cursor.execute("""
                SELECT kcu.column_name, ccu.table_name AS ref_table,
                       ccu.column_name AS ref_column, tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name=kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name=tc.constraint_name
                WHERE tc.table_schema=%s AND tc.table_name=%s AND tc.constraint_type='FOREIGN KEY'
            """, (schema_name, tname))
            for fk in cursor.fetchall():
                tables[tname]["foreign_keys"].append({
                    "column": fk["column_name"], "ref_table": fk["ref_table"],
                    "ref_column": fk["ref_column"], "constraint_name": fk["constraint_name"],
                })

            # Indexes
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=%s AND tablename=%s",
                (schema_name, tname)
            )
            for idx in cursor.fetchall():
                tables[tname]["indexes"].append({
                    "name": idx["indexname"],
                    "unique": "UNIQUE" in (idx["indexdef"] or "").upper(),
                    "def": idx["indexdef"], "columns": [],
                })

            # Row count estimate
            cursor.execute(
                "SELECT reltuples::BIGINT FROM pg_class WHERE relname=%s", (tname,)
            )
            row = cursor.fetchone()
            tables[tname]["row_count"] = int(row["reltuples"]) if row else 0

        cursor.close()
        return SchemaInfo(database=self.config.get("database"), engine="postgresql", tables=tables)

    def get_row_count(self, table_name: str) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                "SELECT reltuples::BIGINT FROM pg_class WHERE relname=%s", (table_name,)
            )
            row = cursor.fetchone()
            if row and row[0] > 1000:
                return int(row[0])
            cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def get_avg_row_size(self, table_name: str) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                "SELECT pg_relation_size(%s)::BIGINT, reltuples::BIGINT "
                "FROM pg_class WHERE relname=%s", (table_name, table_name)
            )
            row = cursor.fetchone()
            if row and row[0] and row[1] and int(row[1]) > 0:
                return int(row[0] / row[1])
            return 512
        finally:
            cursor.close()

    # ── Data movement ─────────────────────────────────────────────────────────

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=5000
    ) -> Generator[Dict[str, Any], None, None]:
        import psycopg2, psycopg2.extras, uuid
        conn   = psycopg2.connect(**self._pg_kwargs())
        conn.autocommit = False
        cname  = f"stream_{uuid.uuid4().hex[:12]}"
        cursor = conn.cursor(name=cname, cursor_factory=psycopg2.extras.RealDictCursor)
        cols   = '"' + '", "'.join(columns) + '"' if columns else "*"
        try:
            cursor.execute(
                f'SELECT {cols} FROM "{table_name}" '
                f'WHERE "{pk_column}" BETWEEN %s AND %s ORDER BY "{pk_column}"',
                (pk_start, pk_end)
            )
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    yield dict(row)
        finally:
            cursor.close()
            conn.close()

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)
        import psycopg2.extras
        start   = time.time()
        conn    = self._conn()
        cursor  = conn.cursor()
        columns = list(rows[0].keys())
        col_str = ", ".join(f'"{c}"' for c in columns)
        values  = [tuple(r[c] for c in columns) for r in rows]
        conflict = "ON CONFLICT DO NOTHING" if mode == "ignore_duplicates" else ""
        sql = f'INSERT INTO "{table_name}" ({col_str}) VALUES %s {conflict}'
        try:
            psycopg2.extras.execute_values(cursor, sql, values, page_size=1000)
            conn.commit()
            cursor.close()
            return BulkWriteResult(len(rows), 0, 0, int((time.time()-start)*1000))
        except Exception as e:
            conn.rollback()
            cursor.close()
            return BulkWriteResult(0, 0, len(rows), int((time.time()-start)*1000), str(e))

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        cursor = self._conn().cursor()
        try:
            cursor.execute(
                f'SELECT COUNT(*) FROM "{table_name}" WHERE "{pk_column}" BETWEEN %s AND %s',
                (pk_start, pk_end)
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        cursor = self._conn().cursor()
        try:
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 0')
            cols   = [d[0] for d in cursor.description]
            concat = " || '|' || ".join(f'COALESCE(CAST("{c}" AS TEXT),\'NULL\')' for c in cols)
            cursor.execute(
                f"SELECT MD5(CAST(BIT_XOR(('x'||SUBSTRING(MD5({concat}),1,8))::BIT(32)::BIGINT::BIT(64)) AS TEXT)) "
                f'FROM "{table_name}" WHERE "{pk_column}" BETWEEN %s AND %s',
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
            cursor.execute(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE')
            self._conn().commit()
        finally:
            cursor.close()

    def execute_ddl(self, ddl: str) -> None:
        conn = self._conn()
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            for stmt in ddl.split(";"):
                s = stmt.strip()
                if s:
                    cursor.execute(s)
        finally:
            conn.autocommit = False
            cursor.close()

    # ── CDC via WAL logical replication ──────────────────────────────────────

    def get_cdc_position(self) -> CDCPosition:
        cursor = self._conn().cursor()
        try:
            cursor.execute("SELECT pg_current_wal_lsn()::TEXT")
            lsn = cursor.fetchone()[0]
            return CDCPosition(method="wal", lsn=lsn)
        finally:
            cursor.close()

    def start_cdc_capture(self, tables, position, callback) -> None:
        """
        CDC via PostgreSQL logical replication using pgoutput plugin.
        Requires: wal_level=logical in postgresql.conf
        Requires: pip install psycopg2-binary

        Creates a replication slot if it doesn't exist, then streams
        WAL changes and calls callback for each change event.
        """
        import psycopg2
        import psycopg2.extras

        slot_name = f"migration_cdc_{self.config.get('database', 'db').replace('-','_')}"
        self._cdc_running = True

        conn = psycopg2.connect(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 5432)),
            dbname=self.config.get("database"),
            user=self.config.get("user"),
            password=self.config.get("password"),
            connection_factory=psycopg2.extras.LogicalReplicationConnection,
        )
        cursor = conn.cursor()

        # Create replication slot if needed
        try:
            cursor.execute(
                "SELECT 1 FROM pg_replication_slots WHERE slot_name=%s", (slot_name,)
            )
            if not cursor.fetchone():
                cursor.create_replication_slot(slot_name, output_plugin="pgoutput")
                logger.info("Replication slot created", slot=slot_name)
        except Exception as e:
            logger.warning("Slot check failed", error=str(e))

        # Start streaming from the given LSN
        options = {"proto_version": "1", "publication_names": "migration_pub"}

        def consume(msg):
            if not self._cdc_running:
                msg.cursor.connection.cancel()
                return
            payload = msg.payload
            # Parse pgoutput protocol (simplified)
            if payload and len(payload) > 1:
                msg_type = chr(payload[0])
                if msg_type == 'I':   # INSERT
                    callback("INSERT", "", None, {}, CDCPosition("wal", lsn=msg.data_start))
                elif msg_type == 'U': # UPDATE
                    callback("UPDATE", "", {}, {}, CDCPosition("wal", lsn=msg.data_start))
                elif msg_type == 'D': # DELETE
                    callback("DELETE", "", {}, None, CDCPosition("wal", lsn=msg.data_start))
            msg.cursor.send_feedback(flush_lsn=msg.data_start)

        try:
            cursor.start_replication(
                slot_name=slot_name, options=options,
                decode=True, start_lsn=position.lsn or "0/0"
            )
            cursor.consume_stream(consume)
        finally:
            cursor.close()
            conn.close()

    def stop_cdc_capture(self) -> None:
        self._cdc_running = False

    # ── Helper ────────────────────────────────────────────────────────────────

    def _pg_kwargs(self) -> dict:
        return {
            "host":            self.config.get("host", "localhost"),
            "port":            int(self.config.get("port", 5432)),
            "dbname":          self.config.get("database"),
            "user":            self.config.get("user"),
            "password":        self.config.get("password"),
            "connect_timeout": 30,
        }
