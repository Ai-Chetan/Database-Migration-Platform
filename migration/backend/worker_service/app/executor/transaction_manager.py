"""
Transaction Manager
File: migration/backend/worker_service/app/executor/transaction_manager.py

Provides a simple context manager for wrapping target DB writes
in a transaction. On success → COMMIT. On failure → ROLLBACK.

Usage:
    with TransactionManager(connection) as txn:
        txn.execute(sql, params)
    # auto-commits on exit, auto-rolls back on exception
"""

from backend.shared.config.logging import logger


class TransactionManager:
    def __init__(self, connection):
        """
        connection: a raw DB-API connection (not SQLAlchemy session).
        For SQLAlchemy sessions, transaction handling is done directly
        in the writer classes using session.begin() / session.rollback().
        """
        self.connection = connection
        self.cursor = None

    def __enter__(self):
        self.cursor = self.connection.cursor()
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # An exception occurred — roll back
            logger.warning("Transaction rolling back due to error", error=str(exc_val))
            self.connection.rollback()
            return False  # Re-raise the exception
        else:
            # Success — commit
            self.connection.commit()
            return True
