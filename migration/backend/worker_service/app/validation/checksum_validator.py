"""
Checksum Validation Engine — Priority 4
File: migration/backend/worker_service/app/validation/checksum_validator.py

Problem it solves:
    Row count alone is not enough to prove data integrity.

    Example:
        Source:   id=1 Alice,  id=2 Bob
        Target:   id=1 Alice,  id=2 NULL

    Row count = 2 in both. Passes count validation.
    But the data is WRONG. Bob's data was lost.

Solution:
    Compute a checksum (MD5 hash) of the actual data values on both sides.
    If source checksum == target checksum → data is identical.
    If they differ → data corruption detected, chunk flagged for investigation.

How the checksum is computed:
    For each row in the chunk:
        hash = MD5(CONCAT(col1, col2, col3, ...))

    Aggregate all row hashes into one chunk-level hash:
        chunk_hash = MD5(SUM(individual row hashes as integers))

    This gives a single hash per chunk that represents ALL the data.

    If even one character changes anywhere in the chunk,
    the chunk hash will be completely different.

Results stored in:
    - migration_chunks.checksum
    - migration_chunks.validation_status ("passed" | "failed")
    - validation_results table (for detailed audit trail)

Usage in chunk_executor.py (after writing data):
    from backend.worker_service.app.validation.checksum_validator import ChecksumValidator

    validator = ChecksumValidator()
    result = validator.validate_chunk(
        source_config=job.source_config,
        target_config=job.target_config,
        table_name=table.table_name,
        pk_column=table.primary_key_column,
        pk_start=chunk.pk_start,
        pk_end=chunk.pk_end
    )

    if result.passed:
        chunk.validation_status = "passed"
        chunk.checksum = result.source_checksum
    else:
        raise Exception(f"Checksum mismatch: {result.details}")
"""

import hashlib
from dataclasses import dataclass
from typing import Optional
import mysql.connector
import psycopg2

from backend.shared.config.logging import logger


@dataclass
class ChecksumResult:
    """Holds the result of a checksum validation."""
    passed: bool
    source_checksum: str
    target_checksum: str
    source_row_count: int
    target_row_count: int
    details: str


class ChecksumValidator:
    """
    Computes and compares checksums between source and target
    for a specific chunk range.
    """

    def validate_chunk(
        self,
        source_config: dict,
        target_config: dict,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> ChecksumResult:
        """
        Main validation method. Computes checksum on source and target,
        compares them, returns a ChecksumResult.
        """
        logger.info(
            "ChecksumValidator: Starting validation",
            table=table_name,
            pk_start=pk_start,
            pk_end=pk_end
        )

        # Compute source checksum
        source_checksum, source_count = self._compute_checksum(
            config=source_config,
            table_name=table_name,
            pk_column=pk_column,
            pk_start=pk_start,
            pk_end=pk_end
        )

        # Compute target checksum
        target_checksum, target_count = self._compute_checksum(
            config=target_config,
            table_name=table_name,
            pk_column=pk_column,
            pk_start=pk_start,
            pk_end=pk_end
        )

        passed = (source_checksum == target_checksum) and (source_count == target_count)

        if passed:
            details = f"Checksums match. Rows: {source_count}"
            logger.info(
                "ChecksumValidator: PASSED",
                table=table_name,
                checksum=source_checksum,
                rows=source_count
            )
        else:
            details = (
                f"MISMATCH. "
                f"Source checksum={source_checksum} rows={source_count} | "
                f"Target checksum={target_checksum} rows={target_count}"
            )
            logger.error(
                "ChecksumValidator: FAILED",
                table=table_name,
                source_checksum=source_checksum,
                target_checksum=target_checksum,
                source_count=source_count,
                target_count=target_count
            )

        return ChecksumResult(
            passed=passed,
            source_checksum=source_checksum,
            target_checksum=target_checksum,
            source_row_count=source_count,
            target_row_count=target_count,
            details=details
        )

    def _compute_checksum(
        self,
        config: dict,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> tuple[str, int]:
        """
        Compute MD5 checksum for a chunk using a native SQL query.
        Uses the DB engine's own MD5 function for speed.

        Returns: (checksum_hex_string, row_count)
        """
        engine = config.get("engine", "").lower()

        if engine == "mysql":
            return self._checksum_mysql(config, table_name, pk_column, pk_start, pk_end)
        elif engine in ("postgres", "postgresql"):
            return self._checksum_postgres(config, table_name, pk_column, pk_start, pk_end)
        else:
            raise ValueError(f"Unsupported engine for checksum: {engine}")

    def _checksum_mysql(
        self,
        config: dict,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> tuple[str, int]:
        """
        MySQL checksum using MD5 and GROUP_CONCAT.

        Strategy:
            1. For each row, compute MD5(CONCAT(all columns))
            2. XOR or SUM all row checksums together
            3. Take MD5 of that aggregate

        We use SUM(CONV(SUBSTRING(MD5(row), 1, 8), 16, 10)) as the aggregate
        because MySQL doesn't have a native way to aggregate MD5s,
        but this approach is deterministic and order-independent.
        """
        conn = mysql.connector.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 3306)),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
            connection_timeout=120
        )
        cursor = conn.cursor()

        try:
            # Get column names
            cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
            columns = [desc[0] for desc in cursor.description]

            # Build CONCAT of all columns (IFNULL handles NULLs)
            concat_expr = ", '|', ".join(
                [f"IFNULL(CAST(`{col}` AS CHAR), 'NULL')" for col in columns]
            )

            # Compute chunk-level checksum
            checksum_sql = f"""
                SELECT
                    MD5(CAST(SUM(CONV(SUBSTRING(MD5(CONCAT({concat_expr})), 1, 8), 16, 10)) AS CHAR)) AS chunk_checksum,
                    COUNT(*) AS row_count
                FROM `{table_name}`
                WHERE `{pk_column}` BETWEEN %s AND %s
            """

            cursor.execute(checksum_sql, (pk_start, pk_end))
            result = cursor.fetchone()

            checksum = result[0] or "empty"
            row_count = result[1] or 0

            return checksum, row_count

        finally:
            cursor.close()
            conn.close()

    def _checksum_postgres(
        self,
        config: dict,
        table_name: str,
        pk_column: str,
        pk_start: int,
        pk_end: int
    ) -> tuple[str, int]:
        """
        PostgreSQL checksum using MD5 and XOR aggregation.

        PostgreSQL has a bit_xor aggregate which is perfect for this.
        XOR of checksums is order-independent and efficient.
        """
        conn = psycopg2.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 5432)),
            dbname=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
            connect_timeout=30
        )
        conn.autocommit = True
        cursor = conn.cursor()

        try:
            # Get column names
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 0')
            columns = [desc[0] for desc in cursor.description]

            # Build COALESCE for each column to handle NULLs
            concat_parts = " || '|' || ".join(
                [f"COALESCE(CAST(\"{col}\" AS TEXT), 'NULL')" for col in columns]
            )

            # Use BIT_XOR aggregate over MD5 hashes
            # This is order-independent (same result regardless of row order)
            checksum_sql = f"""
                SELECT
                    MD5(CAST(
                        BIT_XOR(
                            ('x' || SUBSTRING(MD5({concat_parts}), 1, 8))::BIT(32)::BIGINT::BIT(64)
                        ) AS TEXT
                    )) AS chunk_checksum,
                    COUNT(*) AS row_count
                FROM "{table_name}"
                WHERE "{pk_column}" BETWEEN %s AND %s
            """

            cursor.execute(checksum_sql, (pk_start, pk_end))
            result = cursor.fetchone()

            checksum = result[0] or "empty"
            row_count = result[1] or 0

            return checksum, row_count

        finally:
            cursor.close()
            conn.close()

    def compute_row_level_checksum(self, row: dict) -> str:
        """
        Compute MD5 checksum for a single row dict on the Python side.
        Used for debugging — compare individual rows when chunk checksum fails.

        Example:
            row = {"id": 1, "name": "Alice", "email": "alice@example.com"}
            checksum = validator.compute_row_level_checksum(row)
            # "a3f9b2c1..."
        """
        # Sort by key to ensure consistent ordering
        values = [str(v) if v is not None else "NULL" for _, v in sorted(row.items())]
        row_string = "|".join(values)
        return hashlib.md5(row_string.encode()).hexdigest()
