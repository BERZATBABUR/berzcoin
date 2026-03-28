"""Database migration handling for BerzCoin."""

import time
from typing import List, Callable
from .db import Database
from shared.utils.errors import StorageError
from shared.utils.logging import get_logger

logger = get_logger()

class Migration:
    def __init__(self, version: int, description: str, up: Callable, down: Callable = None):
        self.version = version
        self.description = description
        self.up = up
        self.down = down

    def apply(self, db: Database) -> None:
        logger.info(f"Applying migration {self.version}: {self.description}")
        self.up(db)

    def revert(self, db: Database) -> None:
        if self.down:
            logger.info(f"Reverting migration {self.version}: {self.description}")
            self.down(db)
        else:
            raise StorageError(f"Cannot revert migration {self.version}")

class Migrations:
    def __init__(self, db: Database):
        self.db = db
        self.migrations: List[Migration] = []

    def register(self, version: int, description: str, up: Callable, down: Callable = None) -> None:
        self.migrations.append(Migration(version, description, up, down))
        self.migrations.sort(key=lambda m: m.version)

    def get_applied_migrations(self) -> List[int]:
        try:
            results = self.db.fetch_all("SELECT version FROM migrations ORDER BY version")
            return [r['version'] for r in results]
        except:
            return []

    def record_migration(self, version: int, description: str) -> None:
        self.db.execute("INSERT INTO migrations (version, applied_at, description) VALUES (?, ?, ?)", (version, int(time.time()), description))

    def remove_migration(self, version: int) -> None:
        self.db.execute("DELETE FROM migrations WHERE version = ?", (version,))

    def migrate(self, target_version: int = None) -> None:
        applied = self.get_applied_migrations()
        current = applied[-1] if applied else 0

        if target_version is None:
            target_version = self.migrations[-1].version if self.migrations else current

        logger.info(f"Current version: {current}, Target version: {target_version}")

        if target_version > current:
            pending = [
                m
                for m in self.migrations
                if m.version > current and m.version <= target_version
            ]
            for migration in pending:
                try:
                    migration.apply(self.db)
                    self.record_migration(
                        migration.version, migration.description
                    )
                except Exception as e:
                    raise StorageError(
                        f"Migration {migration.version} failed: {e}"
                    )
            logger.info("Migration complete. Now at version %s", target_version)
        elif target_version < current:
            to_revert = [
                m
                for m in reversed(self.migrations)
                if m.version > target_version and m.version <= current
            ]
            for migration in to_revert:
                try:
                    migration.revert(self.db)
                    self.remove_migration(migration.version)
                except Exception as e:
                    raise StorageError(
                        f"Revert of {migration.version} failed: {e}"
                    )
            logger.info("Revert complete. Now at version %s", target_version)

    def create_migration_table(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                version INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL,
                description TEXT NOT NULL
            )
        """)

def register_standard_migrations(migrations: Migrations) -> None:
    def migration_1_up(db: Database):
        from .schema import Schema
        schema = Schema(db)
        schema.create_tables()
        schema.create_indexes()

    migrations.register(1, "Initial schema", migration_1_up)

    def migration_2_up(db: Database):
        db.execute("CREATE INDEX IF NOT EXISTS idx_outputs_address ON outputs(address)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_utxo_address ON utxo(address)")

    def migration_2_down(db: Database):
        db.execute("DROP INDEX IF EXISTS idx_outputs_address")
        db.execute("DROP INDEX IF EXISTS idx_utxo_address")

    migrations.register(2, "Add address indexes", migration_2_up, migration_2_down)

    def migration_3_up(db: Database):
        cols = [r["name"] for r in db.fetch_all("PRAGMA table_info(block_headers)")]
        if "chainwork" not in cols:
            db.execute(
                "ALTER TABLE block_headers ADD COLUMN chainwork TEXT DEFAULT '0'"
            )

    def migration_3_down(db: Database):
        pass

    migrations.register(3, "Add chainwork to block_headers", migration_3_up, migration_3_down)

    def migration_4_up(db: Database):
        cols = [r["name"] for r in db.fetch_all("PRAGMA table_info(inputs)")]
        if "witness" not in cols:
            db.execute("ALTER TABLE inputs ADD COLUMN witness BLOB")

    def migration_4_down(db: Database):
        pass

    migrations.register(4, "Add witness data to inputs", migration_4_up, migration_4_down)

    def migration_5_up(db: Database):
        cols = [r["name"] for r in db.fetch_all("PRAGMA table_info(transactions)")]
        if "weight" not in cols:
            db.execute(
                "ALTER TABLE transactions ADD COLUMN weight INTEGER DEFAULT 0"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_weight ON transactions(weight)"
        )

    def migration_5_down(db: Database):
        db.execute("DROP INDEX IF EXISTS idx_transactions_weight")

    migrations.register(5, "Add transaction weight", migration_5_up, migration_5_down)

    def migration_6_up(db: Database):
        db.execute("""
            CREATE TABLE IF NOT EXISTS tx_index (
                txid TEXT PRIMARY KEY,
                block_hash TEXT NOT NULL,
                height INTEGER NOT NULL,
                block_time INTEGER NOT NULL,
                block_tx_index INTEGER NOT NULL,
                version INTEGER NOT NULL,
                locktime INTEGER NOT NULL,
                size INTEGER NOT NULL,
                weight INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS tx_inputs (
                txid TEXT NOT NULL,
                input_index INTEGER NOT NULL,
                prev_txid TEXT NOT NULL,
                prev_vout INTEGER NOT NULL,
                script_sig BLOB,
                sequence INTEGER NOT NULL,
                PRIMARY KEY (txid, input_index)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS tx_outputs (
                txid TEXT NOT NULL,
                output_index INTEGER NOT NULL,
                value INTEGER NOT NULL,
                script_pubkey BLOB NOT NULL,
                address TEXT,
                spent INTEGER NOT NULL DEFAULT 0,
                spent_by TEXT,
                PRIMARY KEY (txid, output_index)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_tx_index_height ON tx_index(height)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tx_index_block ON tx_index(block_hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tx_inputs_prev ON tx_inputs(prev_txid, prev_vout)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tx_outputs_address ON tx_outputs(address)")

    def migration_6_down(db: Database):
        db.execute("DROP INDEX IF EXISTS idx_tx_outputs_address")
        db.execute("DROP INDEX IF EXISTS idx_tx_inputs_prev")
        db.execute("DROP INDEX IF EXISTS idx_tx_index_block")
        db.execute("DROP INDEX IF EXISTS idx_tx_index_height")
        db.execute("DROP TABLE IF EXISTS tx_outputs")
        db.execute("DROP TABLE IF EXISTS tx_inputs")
        db.execute("DROP TABLE IF EXISTS tx_index")

    migrations.register(6, "Transaction index tables (tx_index, tx_inputs, tx_outputs)", migration_6_up, migration_6_down)

    def migration_7_up(db: Database):
        # Allow side-branch persistence by removing UNIQUE(height) constraints.
        db.execute("PRAGMA foreign_keys=OFF")

        db.execute("""
            CREATE TABLE IF NOT EXISTS blocks_new (
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
        """)
        db.execute("""
            INSERT OR REPLACE INTO blocks_new
            (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce,
             tx_count, size, weight, is_valid, processed_at)
            SELECT hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce,
                   tx_count, size, weight, is_valid, processed_at
            FROM blocks
        """)
        db.execute("DROP TABLE blocks")
        db.execute("ALTER TABLE blocks_new RENAME TO blocks")

        db.execute("""
            CREATE TABLE IF NOT EXISTS block_headers_new (
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
        """)
        db.execute("""
            INSERT OR REPLACE INTO block_headers_new
            (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid)
            SELECT hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid
            FROM block_headers
        """)
        db.execute("DROP TABLE block_headers")
        db.execute("ALTER TABLE block_headers_new RENAME TO block_headers")

        db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_prev_hash ON blocks(prev_block_hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_block_headers_height ON block_headers(height)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_block_headers_prev_hash ON block_headers(prev_block_hash)")

        db.execute("PRAGMA foreign_keys=ON")

    def migration_7_down(db: Database):
        # Not safely reversible without potentially dropping side-branch data.
        pass

    migrations.register(
        7,
        "Allow non-unique block heights for fork branch persistence",
        migration_7_up,
        migration_7_down,
    )
