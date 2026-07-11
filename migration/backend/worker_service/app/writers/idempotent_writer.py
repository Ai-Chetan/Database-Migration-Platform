"""
Idempotent Bulk Writers — Priority 3
File: migration/backend/worker_service/app/writers/idempotent_writer.py

Problem it solves:
    Chunk #47 executes successfully.
    Worker crashes BEFORE it marks the chunk as COMPLETED in PostgreSQL.
    Stale recovery requeues chunk #47.
    Chunk #47 runs again.
    Now you have DUPLICATE ROWS in the target database.

Solution:
    Every insert must be safe to run multiple times.
    Running the same chunk twice should produce the same result as running it once.
    This property is called IDEMPOTENCY.

Strategy 1 — INSERT IGNORE / ON CONFLICT DO NOTHING (default, recommended):
    MySQL:      INSERT IGNORE INTO users ...
    PostgreSQL: INSERT INTO users ... ON CONFLICT DO NOTHING

    If the row already exists (same primary key), skip it silently.
    No error. No duplicate. Perfect for retries.

Strategy 2 — DELETE + REINSERT (nuclear option):
    DELETE FROM target WHERE pk BETWEEN start AND end
    Then INSERT fresh rows.

    Use this when:
    - The target table has no unique/primary key constraints
    - You need to guarantee the target exactly mirrors the source

This file provides both strategies.
The ChunkExecutor uses Strategy 1 by default.
Strategy 2 is available for special cases.

Usage in chunk_executor.py:
    from backend.worker_service.app.writers.idempotent_writer import (
        IdempotentMySQLWriter,
        IdempotentPostgresWriter
    )
"""

import psycopg2
import psycopg2.extras
import mysql.connector
from typing import List, Dict, Any
from backend.shared.config.logging import logger


# ─── MYSQL IDEMPOTENT WRITER ──────────────────────────────────────────────────

class IdempotentMySQLWriter:
    """
    Writes rows to MySQL with INSERT IGNORE for idempotency.
    Safe to call multiple times with the same data.
    """

    def __init__(self, config: dict):
        self.config = config
        self._connection = None

    def _get_connection(self):
        if self._connection is None or not self._connection.is_connected():
            self._connection = mysql.connector.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 3306)),
                database=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                autocommit=False,
                connection_timeout=300
            )
        return self._connection

    def insert_batch(
        self,
        table_name: str,
        rows: List[Dict[str, Any]]
    ) -> int:
        """
        INSERT IGNORE: if a row with the same primary key already exists,
        skip it silently. Zero duplicates, zero errors on retry.
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        columns = list(rows[0].keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_names = ", ".join([f"`{col}`" for col in columns])

        # INSERT IGNORE is the key difference from standard insert
        insert_sql = f"""
            INSERT IGNORE INTO `{table_name}` ({column_names})
            VALUES ({placeholders})
        """

        values = [tuple(row[col] for col in columns) for row in rows]

        try:
            cursor.executemany(insert_sql, values)
            connection.commit()
            logger.debug(
                "Idempotent MySQL batch inserted (INSERT IGNORE)",
                table=table_name,
                submitted=len(rows),
                actually_inserted=cursor.rowcount
            )
            return len(rows)
        except Exception as e:
            connection.rollback()
            logger.error("Idempotent MySQL insert failed", table=table_name, error=str(e))
            raise
        finally:
            cursor.close()

    def delete_and_reinsert(
        self,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int,
        rows: List[Dict[str, Any]]
    ) -> int:
        """
        Strategy 2: DELETE the range, then INSERT fresh rows.
        Guarantees the target exactly mirrors the source for this chunk.
        Use when the target has no primary key constraints.
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        try:
            # Step 1: Delete any existing rows in this chunk range
            delete_sql = f"""
                DELETE FROM `{table_name}`
                WHERE `{pk_column}` BETWEEN %s AND %s
            """
            cursor.execute(delete_sql, (pk_start, pk_end))
            deleted = cursor.rowcount

            # Step 2: Insert fresh rows
            columns = list(rows[0].keys())
            placeholders = ", ".join(["%s"] * len(columns))
            column_names = ", ".join([f"`{col}`" for col in columns])
            insert_sql = f"INSERT INTO `{table_name}` ({column_names}) VALUES ({placeholders})"
            values = [tuple(row[col] for col in columns) for row in rows]
            cursor.executemany(insert_sql, values)

            connection.commit()

            logger.info(
                "MySQL delete-and-reinsert complete",
                table=table_name,
                deleted=deleted,
                inserted=len(rows)
            )
            return len(rows)
        except Exception as e:
            connection.rollback()
            logger.error("MySQL delete-and-reinsert failed", table=table_name, error=str(e))
            raise
        finally:
            cursor.close()

    def close(self):
        if self._connection and self._connection.is_connected():
            self._connection.close()


# ─── POSTGRESQL IDEMPOTENT WRITER ─────────────────────────────────────────────

class IdempotentPostgresWriter:
    """
    Writes rows to PostgreSQL with ON CONFLICT DO NOTHING for idempotency.
    Safe to call multiple times with the same data.
    """

    def __init__(self, config: dict):
        self.config = config
        self._connection = None

    def _get_connection(self):
        if self._connection is None or self._connection.closed:
            self._connection = psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 5432)),
                dbname=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                connect_timeout=30
            )
            self._connection.autocommit = False
        return self._connection

    def insert_batch(
        self,
        table_name: str,
        rows: List[Dict[str, Any]]
    ) -> int:
        """
        INSERT ... ON CONFLICT DO NOTHING:
        If a row with the same primary key already exists, skip it.
        No error. No duplicate. Perfect for retries.
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        columns = list(rows[0].keys())
        column_names = ", ".join([f'"{col}"' for col in columns])

        # ON CONFLICT DO NOTHING is the key difference
        insert_sql = f"""
            INSERT INTO "{table_name}" ({column_names})
            VALUES %s
            ON CONFLICT DO NOTHING
        """

        values = [tuple(row[col] for col in columns) for row in rows]

        try:
            psycopg2.extras.execute_values(
                cursor,
                insert_sql,
                values,
                page_size=1000
            )
            connection.commit()

            logger.debug(
                "Idempotent PostgreSQL batch inserted (ON CONFLICT DO NOTHING)",
                table=table_name,
                rows=len(rows)
            )
            return len(rows)
        except Exception as e:
            connection.rollback()
            logger.error("Idempotent PostgreSQL insert failed", table=table_name, error=str(e))
            raise
        finally:
            cursor.close()

    def delete_and_reinsert(
        self,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int,
        rows: List[Dict[str, Any]]
    ) -> int:
        """
        Strategy 2: DELETE the range then INSERT fresh.
        Guarantees exact mirror of source for this chunk range.
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        try:
            # Step 1: Delete existing rows in chunk range
            cursor.execute(
                f'DELETE FROM "{table_name}" WHERE "{pk_column}" BETWEEN %s AND %s',
                (pk_start, pk_end)
            )
            deleted = cursor.rowcount

            # Step 2: Insert fresh rows
            columns = list(rows[0].keys())
            column_names = ", ".join([f'"{col}"' for col in columns])
            insert_sql = f'INSERT INTO "{table_name}" ({column_names}) VALUES %s'
            values = [tuple(row[col] for col in columns) for row in rows]

            psycopg2.extras.execute_values(cursor, insert_sql, values, page_size=1000)
            connection.commit()

            logger.info(
                "PostgreSQL delete-and-reinsert complete",
                table=table_name,
                deleted=deleted,
                inserted=len(rows)
            )
            return len(rows)
        except Exception as e:
            connection.rollback()
            logger.error("PostgreSQL delete-and-reinsert failed", table=table_name, error=str(e))
            raise
        finally:
            cursor.close()

    def close(self):
        if self._connection and not self._connection.closed:
            self._connection.close()
