"""Database connection and management for BerzCoin."""

import sqlite3
import os
from typing import Optional, Dict, Any, List, Tuple
from contextlib import contextmanager
from pathlib import Path
from shared.utils.errors import StorageError
from shared.utils.logging import get_logger

logger = get_logger()

class Database:
    """SQLite database connection manager."""
    
    def __init__(self, data_dir: Path, network: str = "mainnet"):
        self.data_dir = data_dir
        self.network = network
        self.db_path = data_dir / f"{network}.db"
        self.connection: Optional[sqlite3.Connection] = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def connect(self) -> None:
        try:
            self.connection = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            logger.info(f"Connected to database: {self.db_path}")
        except Exception as e:
            raise StorageError(f"Failed to connect to database: {e}")
    
    def disconnect(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("Database connection closed")
    
    @contextmanager
    def transaction(self):
        if not self.connection:
            raise StorageError("Database not connected")
        already_in_tx = bool(getattr(self.connection, "in_transaction", False))
        started_tx = False
        try:
            if not already_in_tx:
                self.connection.execute("BEGIN TRANSACTION")
                started_tx = True
            yield self.connection
            if started_tx:
                self.connection.commit()
        except Exception as e:
            if started_tx:
                self.connection.rollback()
            raise StorageError(f"Transaction failed: {e}")
    
    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        if not self.connection:
            raise StorageError("Database not connected")
        try:
            cursor = self.connection.execute(query, params)
            return cursor
        except sqlite3.Error as e:
            raise StorageError(f"Query failed: {e}\nQuery: {query}")
    
    def executemany(self, query: str, params: List[tuple]) -> sqlite3.Cursor:
        if not self.connection:
            raise StorageError("Database not connected")
        try:
            cursor = self.connection.executemany(query, params)
            return cursor
        except sqlite3.Error as e:
            raise StorageError(f"Batch query failed: {e}")
    
    def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cursor = self.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def vacuum(self) -> None:
        self.execute("VACUUM")
        logger.info("Database vacuumed")
    
    def backup(self, backup_path: Path) -> None:
        if not self.connection:
            raise StorageError("Database not connected")
        try:
            backup = sqlite3.connect(str(backup_path))
            self.connection.backup(backup)
            backup.close()
            logger.info(f"Database backed up to {backup_path}")
        except Exception as e:
            raise StorageError(f"Backup failed: {e}")
    
    def get_size(self) -> int:
        if not self.db_path.exists():
            return 0
        return self.db_path.stat().st_size
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
