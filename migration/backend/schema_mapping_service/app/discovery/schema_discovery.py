"""
Schema Discovery Engine
File: migration/backend/schema_mapping_service/app/discovery/schema_discovery.py

Discovers the full schema of a live MySQL or PostgreSQL database.
Also supports parsing the original tool's text-based schema files.

Returns a unified dict structure used by all other components:
{
  "database": "mydb",
  "engine":   "mysql",
  "tables": {
    "users": {
      "columns": {
        "id":    {"type": "int(11)", "nullable": False, "pk": True, "default": None, "extra": "auto_increment"},
        "email": {"type": "varchar(255)", "nullable": False, "pk": False, "default": None, "extra": "", "unique": True}
      },
      "primary_keys": ["id"],
      "foreign_keys": [{"column": "role_id", "ref_table": "roles", "ref_column": "id"}],
      "indexes":      [{"name": "idx_email", "columns": ["email"], "unique": True}],
      "row_count":    150000
    }
  }
}
"""

import re
import os
from typing import Dict, Any
from backend.shared.config.logging import logger


class SchemaDiscovery:

    def __init__(self, config: dict):
        self.config = config
        self.engine = config.get("engine", "").lower()

    def discover(self) -> Dict[str, Any]:
        logger.info("Schema discovery starting", engine=self.engine, db=self.config.get("database"))
        if self.engine == "mysql":
            return self._discover_mysql()
        elif self.engine in ("postgres", "postgresql"):
            return self._discover_postgres()
        else:
            raise ValueError(f"Unsupported engine for discovery: {self.engine}")

    # ── MySQL ──────────────────────────────────────────────────────────────────

    def _discover_mysql(self) -> Dict[str, Any]:
        import mysql.connector

        conn = mysql.connector.connect(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 3306)),
            database=self.config.get("database"),
            user=self.config.get("user"),
            password=self.config.get("password"),
            connection_timeout=30
        )
        cursor = conn.cursor(dictionary=True)
        db_name = self.config.get("database")

        try:
            result = {"database": db_name, "engine": "mysql", "tables": {}}

            # All user tables
            cursor.execute(
                "SELECT TABLE_NAME, TABLE_ROWS "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_NAME",
                (db_name,)
            )
            tables = cursor.fetchall()

            for table_row in tables:
                tname = table_row["TABLE_NAME"]
                tdata = {
                    "columns": {},
                    "primary_keys": [],
                    "foreign_keys": [],
                    "indexes": [],
                    "row_count": int(table_row.get("TABLE_ROWS") or 0)
                }

                # Columns
                cursor.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, "
                    "COLUMN_DEFAULT, EXTRA "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (db_name, tname)
                )
                for col in cursor.fetchall():
                    tdata["columns"][col["COLUMN_NAME"]] = {
                        "type":     col["COLUMN_TYPE"],
                        "nullable": col["IS_NULLABLE"] == "YES",
                        "pk":       col["COLUMN_KEY"] == "PRI",
                        "unique":   col["COLUMN_KEY"] == "UNI",
                        "default":  col["COLUMN_DEFAULT"],
                        "extra":    col["EXTRA"] or "",
                    }
                    if col["COLUMN_KEY"] == "PRI":
                        tdata["primary_keys"].append(col["COLUMN_NAME"])

                # Foreign keys
                cursor.execute(
                    "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, CONSTRAINT_NAME "
                    "FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "AND REFERENCED_TABLE_NAME IS NOT NULL",
                    (db_name, tname)
                )
                for fk in cursor.fetchall():
                    tdata["foreign_keys"].append({
                        "column":          fk["COLUMN_NAME"],
                        "ref_table":       fk["REFERENCED_TABLE_NAME"],
                        "ref_column":      fk["REFERENCED_COLUMN_NAME"],
                        "constraint_name": fk["CONSTRAINT_NAME"],
                    })

                # Indexes
                cursor.execute(
                    "SELECT INDEX_NAME, "
                    "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') AS cols, "
                    "NON_UNIQUE "
                    "FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "GROUP BY INDEX_NAME, NON_UNIQUE",
                    (db_name, tname)
                )
                for idx in cursor.fetchall():
                    tdata["indexes"].append({
                        "name":    idx["INDEX_NAME"],
                        "columns": idx["cols"].split(",") if idx["cols"] else [],
                        "unique":  idx["NON_UNIQUE"] == 0,
                    })

                result["tables"][tname] = tdata

            logger.info("MySQL discovery complete", db=db_name, tables=len(result["tables"]))
            return result

        finally:
            cursor.close()
            conn.close()

    # ── PostgreSQL ─────────────────────────────────────────────────────────────

    def _discover_postgres(self) -> Dict[str, Any]:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(
            host=self.config.get("host", "localhost"),
            port=int(self.config.get("port", 5432)),
            dbname=self.config.get("database"),
            user=self.config.get("user"),
            password=self.config.get("password"),
            connect_timeout=30
        )
        conn.autocommit = True
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        schema_name = self.config.get("pg_schema", "public")

        try:
            result = {"database": self.config.get("database"), "engine": "postgresql", "tables": {}}

            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
                (schema_name,)
            )
            table_names = [row["tablename"] for row in cursor.fetchall()]

            for tname in table_names:
                tdata = {"columns": {}, "primary_keys": [], "foreign_keys": [], "indexes": [], "row_count": 0}

                # Columns
                cursor.execute("""
                    SELECT
                        c.column_name,
                        c.udt_name,
                        c.character_maximum_length,
                        c.numeric_precision,
                        c.numeric_scale,
                        c.is_nullable,
                        c.column_default,
                        CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_pk
                    FROM information_schema.columns c
                    LEFT JOIN (
                        SELECT kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                            AND tc.table_schema = kcu.table_schema
                        WHERE tc.table_schema = %s AND tc.table_name = %s
                          AND tc.constraint_type = 'PRIMARY KEY'
                    ) pk ON c.column_name = pk.column_name
                    WHERE c.table_schema = %s AND c.table_name = %s
                    ORDER BY c.ordinal_position
                """, (schema_name, tname, schema_name, tname))

                for col in cursor.fetchall():
                    col = dict(col)
                    base = col["udt_name"]
                    if col["character_maximum_length"]:
                        full_type = f"{base}({col['character_maximum_length']})"
                    elif col["numeric_precision"] and col["numeric_scale"] is not None:
                        full_type = f"{base}({col['numeric_precision']},{col['numeric_scale']})"
                    else:
                        full_type = base

                    tdata["columns"][col["column_name"]] = {
                        "type":     full_type,
                        "nullable": col["is_nullable"] == "YES",
                        "pk":       bool(col["is_pk"]),
                        "unique":   False,
                        "default":  col["column_default"],
                        "extra":    "",
                    }
                    if col["is_pk"]:
                        tdata["primary_keys"].append(col["column_name"])

                # Foreign keys
                cursor.execute("""
                    SELECT kcu.column_name, ccu.table_name AS ref_table,
                           ccu.column_name AS ref_column, tc.constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.table_schema = %s AND tc.table_name = %s
                      AND tc.constraint_type = 'FOREIGN KEY'
                """, (schema_name, tname))

                for fk in cursor.fetchall():
                    tdata["foreign_keys"].append({
                        "column": fk["column_name"], "ref_table": fk["ref_table"],
                        "ref_column": fk["ref_column"], "constraint_name": fk["constraint_name"],
                    })

                # Indexes
                cursor.execute(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = %s AND tablename = %s",
                    (schema_name, tname)
                )
                for idx in cursor.fetchall():
                    tdata["indexes"].append({
                        "name": idx["indexname"],
                        "unique": "UNIQUE" in (idx["indexdef"] or "").upper(),
                        "def": idx["indexdef"],
                        "columns": [],
                    })

                # Row count estimate
                cursor.execute("SELECT reltuples::BIGINT FROM pg_class WHERE relname = %s", (tname,))
                row = cursor.fetchone()
                tdata["row_count"] = int(row["reltuples"]) if row else 0

                result["tables"][tname] = tdata

            logger.info("PostgreSQL discovery complete", tables=len(result["tables"]))
            return result

        finally:
            cursor.close()
            conn.close()


def parse_schema_file(file_path: str) -> Dict[str, Any]:
    """
    Parse the original tool's plain-text schema file format.
    Returns same unified structure as SchemaDiscovery.discover().

    File format:
        Table: users
          id INT AUTO_INCREMENT PRIMARY KEY
          name VARCHAR(255) NOT NULL
    """
    if not file_path or not os.path.exists(file_path):
        return {"tables": {}, "source_type": "file"}

    tables: Dict[str, Any] = {}
    current_table = None

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("--"):
                continue
            match = re.match(r"Table:\s*(\w+)", line, re.IGNORECASE)
            if match:
                current_table = match.group(1)
                tables[current_table] = {
                    "columns": {}, "primary_keys": [],
                    "foreign_keys": [], "indexes": [], "row_count": 0
                }
                continue
            if current_table:
                col_match = re.match(r"[`']?([\w_]+)[`']?\s+(.+)", line)
                if col_match:
                    col_name = col_match.group(1)
                    definition = col_match.group(2).strip()
                    parts = definition.split()
                    col_type = parts[0] if parts else ""
                    is_pk = "PRIMARY KEY" in definition.upper()
                    tables[current_table]["columns"][col_name] = {
                        "type":     col_type,
                        "nullable": "NOT NULL" not in definition.upper(),
                        "pk":       is_pk,
                        "unique":   "UNIQUE" in definition.upper(),
                        "default":  None,
                        "extra":    "auto_increment" if "AUTO_INCREMENT" in definition.upper() else "",
                        "raw_def":  definition,
                    }
                    if is_pk:
                        tables[current_table]["primary_keys"].append(col_name)

    return {"tables": tables, "engine": "file", "source_type": "file"}
