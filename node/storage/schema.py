"""Database schema definitions for BerzCoin."""

from typing import List, Dict, Any
from .db import Database
from shared.utils.logging import get_logger

logger = get_logger()

class Schema:
    """Database schema manager."""
    CURRENT_VERSION = 1
    TABLES = {
        "blocks": """
            CREATE TABLE IF NOT EXISTS blocks (
                hash TEXT PRIMARY KEY,
                height INTEGER NOT NULL,
                version INTEGER NOT NULL,
                prev_block_hash TEXT NOT NULL,
                merkle_root TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                bits INTEGER NOT NULL,
                nonce INTEGER NOT NULL,
                tx_count INTEGER NOT NULL,
                size INTEGER NOT NULL,
                weight INTEGER NOT NULL,
                is_valid BOOLEAN NOT NULL,
                processed_at INTEGER NOT NULL
            )
        """,
        "block_headers": """
            CREATE TABLE IF NOT EXISTS block_headers (
                hash TEXT PRIMARY KEY,
                height INTEGER NOT NULL,
                version INTEGER NOT NULL,
                prev_block_hash TEXT NOT NULL,
                merkle_root TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                bits INTEGER NOT NULL,
                nonce INTEGER NOT NULL,
                chainwork TEXT NOT NULL,
                is_valid BOOLEAN NOT NULL
            )
        """,
        "transactions": """
            CREATE TABLE IF NOT EXISTS transactions (
                txid TEXT PRIMARY KEY,
                block_hash TEXT NOT NULL,
                height INTEGER NOT NULL,
                "index" INTEGER NOT NULL,
                version INTEGER NOT NULL,
                locktime INTEGER NOT NULL,
                size INTEGER NOT NULL,
                weight INTEGER NOT NULL,
                is_coinbase BOOLEAN NOT NULL,
                FOREIGN KEY (block_hash) REFERENCES blocks(hash)
            )
        """,
        "inputs": """
            CREATE TABLE IF NOT EXISTS inputs (
                txid TEXT NOT NULL,
                "index" INTEGER NOT NULL,
                prev_txid TEXT NOT NULL,
                prev_index INTEGER NOT NULL,
                script_sig BLOB,
                sequence INTEGER NOT NULL,
                witness BLOB,
                PRIMARY KEY (txid, "index"),
                FOREIGN KEY (txid) REFERENCES transactions(txid)
            )
        """,
        "outputs": """
            CREATE TABLE IF NOT EXISTS outputs (
                txid TEXT NOT NULL,
                "index" INTEGER NOT NULL,
                value INTEGER NOT NULL,
                script_pubkey BLOB NOT NULL,
                address TEXT,
                spent BOOLEAN NOT NULL DEFAULT 0,
                spent_by_txid TEXT,
                spent_by_index INTEGER,
                PRIMARY KEY (txid, "index"),
                FOREIGN KEY (txid) REFERENCES transactions(txid)
            )
        """,
        "utxo": """
            CREATE TABLE IF NOT EXISTS utxo (
                outpoint TEXT PRIMARY KEY,
                txid TEXT NOT NULL,
                "index" INTEGER NOT NULL,
                value INTEGER NOT NULL,
                script_pubkey BLOB NOT NULL,
                address TEXT,
                height INTEGER NOT NULL,
                is_coinbase BOOLEAN NOT NULL
            )
        """,
        "peers": """
            CREATE TABLE IF NOT EXISTS peers (
                address TEXT PRIMARY KEY,
                port INTEGER NOT NULL,
                services INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                last_connected INTEGER,
                connected_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                banned_until INTEGER DEFAULT 0,
                version INTEGER,
                user_agent TEXT,
                height INTEGER,
                is_seed BOOLEAN DEFAULT 0
            )
        """,
        "bans": """
            CREATE TABLE IF NOT EXISTS bans (
                address TEXT PRIMARY KEY,
                banned_until INTEGER NOT NULL,
                reason TEXT,
                banned_at INTEGER NOT NULL
            )
        """,
        "checkpoints": """
            CREATE TABLE IF NOT EXISTS checkpoints (
                height INTEGER PRIMARY KEY,
                hash TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """,
        "migrations": """
            CREATE TABLE IF NOT EXISTS migrations (
                version INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL,
                description TEXT NOT NULL
            )
        """,
        "settings": """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """
    }
    INDEXES = {
        "idx_blocks_height": "CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height)",
        "idx_blocks_hash": "CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash)",
        "idx_blocks_prev_hash": "CREATE INDEX IF NOT EXISTS idx_blocks_prev_hash ON blocks(prev_block_hash)",
        "idx_blocks_timestamp": "CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp)",
        "idx_block_headers_height": "CREATE INDEX IF NOT EXISTS idx_block_headers_height ON block_headers(height)",
        "idx_block_headers_prev_hash": "CREATE INDEX IF NOT EXISTS idx_block_headers_prev_hash ON block_headers(prev_block_hash)",
        "idx_transactions_block": "CREATE INDEX IF NOT EXISTS idx_transactions_block ON transactions(block_hash)",
        "idx_transactions_height": "CREATE INDEX IF NOT EXISTS idx_transactions_height ON transactions(height)",
        "idx_transactions_txid": "CREATE INDEX IF NOT EXISTS idx_transactions_txid ON transactions(txid)",
        "idx_inputs_prev": "CREATE INDEX IF NOT EXISTS idx_inputs_prev ON inputs(prev_txid, prev_index)",
        "idx_outputs_address": "CREATE INDEX IF NOT EXISTS idx_outputs_address ON outputs(address)",
        "idx_outputs_spent": "CREATE INDEX IF NOT EXISTS idx_outputs_spent ON outputs(spent)",
        "idx_outputs_txid": "CREATE INDEX IF NOT EXISTS idx_outputs_txid ON outputs(txid)",
        "idx_utxo_address": "CREATE INDEX IF NOT EXISTS idx_utxo_address ON utxo(address)",
        "idx_utxo_height": "CREATE INDEX IF NOT EXISTS idx_utxo_height ON utxo(height)",
        "idx_peers_last_seen": "CREATE INDEX IF NOT EXISTS idx_peers_last_seen ON peers(last_seen)",
        "idx_peers_banned": "CREATE INDEX IF NOT EXISTS idx_peers_banned ON peers(banned_until)"
    }

    def __init__(self, db: Database):
        self.db = db

    def create_tables(self) -> None:
        with self.db.transaction():
            for table_name, create_sql in self.TABLES.items():
                self.db.execute(create_sql)
                logger.debug(f"Table created/verified: {table_name}")

    def create_indexes(self) -> None:
        with self.db.transaction():
            for index_name, create_sql in self.INDEXES.items():
                self.db.execute(create_sql)
                logger.debug(f"Index created/verified: {index_name}")

    def init_schema(self) -> None:
        self.create_tables()
        self.create_indexes()
        self.set_schema_version(self.CURRENT_VERSION)
        logger.info(f"Database schema initialized (version {self.CURRENT_VERSION})")

    def get_schema_version(self) -> int:
        try:
            result = self.db.fetch_one("SELECT value FROM settings WHERE key = 'schema_version'")
            return int(result['value']) if result else 0
        except:
            return 0

    def set_schema_version(self, version: int) -> None:
        import time
        self.db.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)", ("schema_version", str(version), int(time.time())))

    def table_exists(self, table_name: str) -> bool:
        result = self.db.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return result is not None

    def get_table_info(self, table_name: str) -> List[Dict[str, Any]]:
        return self.db.fetch_all(f"PRAGMA table_info({table_name})")

    def get_table_size(self, table_name: str) -> int:
        result = self.db.fetch_one(f"SELECT COUNT(*) as count FROM {table_name}")
        return result['count'] if result else 0
