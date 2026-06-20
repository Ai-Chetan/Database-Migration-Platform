"""
MySQL Reader - Source Data Streaming
File: migration/backend/worker_service/app/readers/mysql_reader.py

Reads rows from a MySQL source database in a memory-safe,
streaming fashion using server-side cursors.

CRITICAL: Never use fetchall() here.
For a 50M row table, fetchall() would crash memory.
We use fetchmany(chunk_size) to stream rows in small batches.

source_config example:
{
    "engine": "mysql",
    "host": "localhost",
    "port": 3306,
    "database": "source_db",
    "user": "root",
    "password": "secret"
}
"""

import mysql.connector
from typing import Generator, Dict, Any, List
from backend.shared.config.logging import logger


# How many rows to fetch from MySQL at a time
# This controls memory usage. 5000 rows is a safe default.
FETCH_BATCH_SIZE = 5000


class MySQLReader:
    def __init__(self, config: dict):
        self.config = config
        self._connection = None

    def _get_connection(self):
        """Create and return a MySQL connection."""
        if self._connection is None or not self._connection.is_connected():
            self._connection = mysql.connector.connect(
                host=self.config.get("host", "localhost"),
                port=int(self.config.get("port", 3306)),
                database=self.config.get("database"),
                user=self.config.get("user"),
                password=self.config.get("password"),
                # Use buffered=False for streaming (server-side cursor)
                buffered=False,
                # Keep connection alive for long-running chunks
                connection_timeout=300
            )
            logger.info(
                "MySQL source connection established",
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
        Stream rows from a MySQL table within the given primary key range.

        This is a Python generator - it yields one row at a time.
        The caller (ChunkExecutor) accumulates rows into batches.

        Example query generated:
            SELECT * FROM users WHERE id BETWEEN 1 AND 100000 ORDER BY id

        Yields:
            dict: {"id": 1, "name": "Alice", "email": "alice@example.com"}
        """
        connection = self._get_connection()

        # Use dictionary=True so rows come back as dicts (not tuples)
        # Use buffered=False for server-side streaming (memory safe)
        cursor = connection.cursor(dictionary=True, buffered=False)

        query = f"""
            SELECT *
            FROM `{table_name}`
            WHERE `{pk_column}` BETWEEN %s AND %s
            ORDER BY `{pk_column}`
        """

        logger.info(
            "Streaming MySQL chunk",
            table=table_name,
            pk_start=pk_start,
            pk_end=pk_end
        )

        try:
            cursor.execute(query, (pk_start, pk_end))

            rows_yielded = 0

            # fetchmany streams in batches without loading everything into memory
            while True:
                rows = cursor.fetchmany(FETCH_BATCH_SIZE)
                if not rows:
                    break  # No more rows

                for row in rows:
                    yield row
                    rows_yielded += 1

            logger.info(
                "MySQL chunk streaming complete",
                table=table_name,
                total_rows=rows_yielded
            )

        except Exception as e:
            logger.error(
                "MySQL streaming failed",
                table=table_name,
                pk_start=pk_start,
                pk_end=pk_end,
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
        """
        Count rows in the source database for a given chunk range.
        Used for validation after writing to target.
        """
        connection = self._get_connection()
        cursor = connection.cursor()

        query = f"""
            SELECT COUNT(*)
            FROM `{table_name}`
            WHERE `{pk_column}` BETWEEN %s AND %s
        """

        try:
            cursor.execute(query, (pk_start, pk_end))
            result = cursor.fetchone()
            count = result[0] if result else 0
            return count
        finally:
            cursor.close()

    def get_column_names(self, table_name: str) -> List[str]:
        """
        Returns the list of column names for a table.
        Used by the writer to know the insert column order.
        """
        connection = self._get_connection()
        cursor = connection.cursor()

        query = f"SELECT * FROM `{table_name}` LIMIT 0"

        try:
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description]
            return columns
        finally:
            cursor.close()

    def close(self):
        """Close the MySQL connection."""
        if self._connection and self._connection.is_connected():
            self._connection.close()
            logger.info("MySQL source connection closed")
