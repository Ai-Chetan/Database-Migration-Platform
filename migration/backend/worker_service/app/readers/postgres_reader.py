"""
PostgreSQL Reader - Source Data Streaming
File: migration/backend/worker_service/app/readers/postgres_reader.py

Reads rows from a PostgreSQL source database in a memory-safe,
streaming fashion using named server-side cursors.

PostgreSQL server-side cursors are superior to MySQL's fetchmany()
because they truly live on the server and use minimal client memory.

source_config example:
{
    "engine": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database": "source_db",
    "user": "postgres",
    "password": "secret"
}
"""

import psycopg2
import psycopg2.extras
import uuid
from typing import Generator, Dict, Any, List
from backend.shared.config.logging import logger


# Fetch this many rows from the server cursor at a time
FETCH_BATCH_SIZE = 5000


class PostgresReader:
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
                connect_timeout=30,
                # Use a long statement timeout for large chunks
                options="-c statement_timeout=600000"  # 10 min timeout
            )
            # Auto-commit must be OFF for server-side cursors to work
            self._connection.autocommit = False

            logger.info(
                "PostgreSQL source connection established",
                host=self.config.get("host"),
                database=self.config.get("database")
            )
        return self._connection

    def stream_chunk(
        self,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream rows from a PostgreSQL table using a named server-side cursor.

        Named cursors in psycopg2 are server-side and never load all rows
        into client memory - perfect for large tables.

        Example query:
            SELECT * FROM users WHERE id BETWEEN 1 AND 100000 ORDER BY id

        Yields:
            dict: {"id": 1, "name": "Alice", "email": "alice@example.com"}
        """
        connection = self._get_connection()

        # Give cursor a unique name to make it a server-side cursor
        cursor_name = f"worker_cursor_{uuid.uuid4().hex[:12]}"

        # Use RealDictCursor so rows come back as dicts
        cursor = connection.cursor(
            name=cursor_name,
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        query = f"""
            SELECT *
            FROM "{table_name}"
            WHERE "{pk_column}" BETWEEN %s AND %s
            ORDER BY "{pk_column}"
        """

        logger.info(
            "Streaming PostgreSQL chunk",
            table=table_name,
            pk_start=pk_start,
            pk_end=pk_end
        )

        try:
            cursor.execute(query, (pk_start, pk_end))

            rows_yielded = 0

            while True:
                rows = cursor.fetchmany(FETCH_BATCH_SIZE)
                if not rows:
                    break

                for row in rows:
                    # RealDictRow → convert to regular dict
                    yield dict(row)
                    rows_yielded += 1

            logger.info(
                "PostgreSQL chunk streaming complete",
                table=table_name,
                total_rows=rows_yielded
            )

        except Exception as e:
            connection.rollback()
            logger.error(
                "PostgreSQL streaming failed",
                table=table_name,
                error=str(e)
            )
            raise
        finally:
            cursor.close()

    def count_rows(
        self,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> int:
        """Count rows in source PostgreSQL for validation."""
        connection = self._get_connection()
        cursor = connection.cursor()

        query = f"""
            SELECT COUNT(*)
            FROM "{table_name}"
            WHERE "{pk_column}" BETWEEN %s AND %s
        """

        try:
            cursor.execute(query, (pk_start, pk_end))
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()

    def get_column_names(self, table_name: str) -> List[str]:
        """Returns column names for a table."""
        connection = self._get_connection()
        cursor = connection.cursor()

        try:
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 0')
            return [desc[0] for desc in cursor.description]
        finally:
            cursor.close()

    def close(self):
        """Close the PostgreSQL connection."""
        if self._connection and not self._connection.closed:
            self._connection.close()
            logger.info("PostgreSQL source connection closed")
