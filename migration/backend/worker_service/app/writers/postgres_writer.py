"""
PostgreSQL Writer - Bulk Insert Engine
File: migration/backend/worker_service/app/writers/postgres_writer.py

Writes rows to a PostgreSQL target database using efficient bulk inserts.

Uses psycopg2's execute_values() which is the fastest way to insert
multiple rows into PostgreSQL. It's significantly faster than:
- Individual INSERT statements
- executemany()
- multi-value INSERT strings

target_config example:
{
    "engine": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database": "target_db",
    "user": "postgres",
    "password": "secret"
}
"""

import psycopg2
import psycopg2.extras
from typing import List, Dict, Any
from backend.shared.config.logging import logger


class PostgresWriter:
    def __init__(self, config: dict):
        self.config = config
        self._connection = None

    def _get_connection(self):
        """Create and return a PostgreSQL connection."""
        if self._connection is None or self._connection.closed:
            self._connection = psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 5432)),
                dbname=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                connect_timeout=30
            )
            # autocommit=False — we control transactions manually
            self._connection.autocommit = False

            logger.info(
                "PostgreSQL target connection established",
                host=self.config.get("host"),
                database=self.config.get("database")
            )
        return self._connection

    def insert_batch(
        self,
        table_name: str,
        rows: List[Dict[str, Any]]
    ) -> int:
        """
        Bulk insert rows into PostgreSQL using execute_values().

        execute_values() is the recommended high-performance method
        for multi-row inserts in psycopg2.

        Example generated SQL:
            INSERT INTO users (id, name, email) VALUES
            (1, 'Alice', 'alice@ex.com'),
            (2, 'Bob',   'bob@ex.com'),
            ...

        Returns:
            int: number of rows inserted
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        # Get column names from first row
        columns = list(rows[0].keys())
        column_names = ", ".join([f'"{col}"' for col in columns])

        insert_sql = f'INSERT INTO "{table_name}" ({column_names}) VALUES %s'

        # Convert list of dicts → list of tuples
        values = [tuple(row[col] for col in columns) for row in rows]

        try:
            # execute_values is the fastest bulk insert method in psycopg2
            # page_size controls how many rows go into each internal batch
            psycopg2.extras.execute_values(
                cursor,
                insert_sql,
                values,
                page_size=1000  # Internal batching within execute_values
            )
            connection.commit()

            logger.debug(
                "Batch inserted to PostgreSQL target",
                table=table_name,
                rows=len(rows)
            )
            return len(rows)

        except psycopg2.errors.UniqueViolation as e:
            # This can happen on retries if the chunk was partially written
            # before a crash. For MVP we log and skip. Later: use ON CONFLICT.
            connection.rollback()
            logger.warning(
                "Unique constraint violation during insert - possible retry duplicate",
                table=table_name,
                error=str(e)
            )
            # Re-raise so executor can handle it
            raise

        except Exception as e:
            connection.rollback()
            logger.error(
                "PostgreSQL batch insert failed",
                table=table_name,
                error=str(e),
                batch_size=len(rows)
            )
            raise
        finally:
            cursor.close()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the target PostgreSQL database."""
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table_name,)
            )
            result = cursor.fetchone()
            return result[0] > 0
        finally:
            cursor.close()

    def close(self):
        """Close the PostgreSQL target connection."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            logger.info("PostgreSQL target connection closed")
