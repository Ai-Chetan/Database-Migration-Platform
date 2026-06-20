"""
MySQL Writer - Bulk Insert Engine
File: migration/backend/worker_service/app/writers/mysql_writer.py

Writes rows to a MySQL target database using bulk INSERT statements.

Key principle: NEVER insert one row at a time.
Use multi-row INSERT for high throughput:
    INSERT INTO users (id, name, email) VALUES
        (1, 'Alice', 'alice@example.com'),
        (2, 'Bob',   'bob@example.com'),
        ...
        (5000, 'Zara', 'zara@example.com')

This is 100x faster than single-row inserts.

target_config example:
{
    "engine": "mysql",
    "host": "localhost",
    "port": 3306,
    "database": "target_db",
    "user": "root",
    "password": "secret"
}
"""

import mysql.connector
from typing import List, Dict, Any
from backend.shared.config.logging import logger


class MySQLWriter:
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
                # autocommit=False so we control transactions
                autocommit=False,
                connection_timeout=300
            )
            logger.info(
                "MySQL target connection established",
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
        Bulk insert a list of row dicts into the target table.

        Uses executemany() which builds a multi-row INSERT statement.
        Wrapped in a transaction: COMMIT on success, ROLLBACK on failure.

        Returns:
            int: number of rows successfully inserted
        """
        if not rows:
            return 0

        connection = self._get_connection()
        cursor = connection.cursor()

        # Extract column names from the first row
        columns = list(rows[0].keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_names = ", ".join([f"`{col}`" for col in columns])

        insert_sql = f"""
            INSERT INTO `{table_name}` ({column_names})
            VALUES ({placeholders})
        """

        # Convert list of dicts to list of tuples (required by executemany)
        values = [tuple(row[col] for col in columns) for row in rows]

        try:
            cursor.executemany(insert_sql, values)
            connection.commit()

            rows_inserted = cursor.rowcount
            logger.debug(
                "Batch inserted to MySQL target",
                table=table_name,
                rows=rows_inserted
            )
            return len(rows)  # rowcount can be unreliable for executemany

        except Exception as e:
            connection.rollback()
            logger.error(
                "MySQL batch insert failed",
                table=table_name,
                error=str(e),
                batch_size=len(rows)
            )
            raise
        finally:
            cursor.close()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the target database."""
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table_name,)
            )
            result = cursor.fetchone()
            return result[0] > 0
        finally:
            cursor.close()

    def close(self):
        """Close the MySQL target connection."""
        if self._connection and self._connection.is_connected():
            self._connection.close()
            logger.info("MySQL target connection closed")
