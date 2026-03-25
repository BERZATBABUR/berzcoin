"""UTXO set storage management."""

from typing import List, Optional, Dict, Any
from shared.utils.logging import get_logger
from .db import Database

logger = get_logger()

class UTXOStore:
    """UTXO set storage manager."""

    def __init__(self, db: Database):
        self.db = db

    def add_utxo(self, txid: str, index: int, value: int, script_pubkey: bytes,
                 height: int, is_coinbase: bool) -> None:
        outpoint = f"{txid}:{index}"
        self.db.execute("""
            INSERT OR REPLACE INTO utxo
            (outpoint, txid, "index", value, script_pubkey, height, is_coinbase)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (outpoint, txid, index, value, script_pubkey, height, is_coinbase))

    def spend_utxo(self, txid: str, index: int) -> bool:
        outpoint = f"{txid}:{index}"
        result = self.db.execute("DELETE FROM utxo WHERE outpoint = ?", (outpoint,))
        if result.rowcount > 0:
            self.db.execute("""
                UPDATE outputs SET spent = 1, spent_by_txid = ?
                WHERE txid = ? AND "index" = ?
            """, (txid, txid, index))
            return True
        return False

    def get_utxo(self, txid: str, index: int) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one("SELECT * FROM utxo WHERE outpoint = ?", (f"{txid}:{index}",))

    def get_utxos_for_address(self, address: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.fetch_all("""
            SELECT * FROM utxo
            WHERE address = ?
            ORDER BY value DESC
            LIMIT ?
        """, (address, limit))

    def get_utxos_for_transaction(self, txid: str) -> List[Dict[str, Any]]:
        return self.db.fetch_all('SELECT * FROM utxo WHERE txid = ? ORDER BY "index"', (txid,))

    def get_balance(self, address: str) -> int:
        result = self.db.fetch_one("SELECT SUM(value) as total FROM utxo WHERE address = ?", (address,))
        return result['total'] if result and result['total'] else 0

    def get_utxo_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(*) as count FROM utxo")
        return result['count'] if result else 0

    def get_total_value(self) -> int:
        result = self.db.fetch_one("SELECT SUM(value) as total FROM utxo")
        return result['total'] if result and result['total'] else 0

    def prune_coinbase_utxos(self, current_height: int, maturity: int = 100) -> int:
        result = self.db.execute("""
            DELETE FROM utxo
            WHERE is_coinbase = 1 AND height < ? - ?
        """, (current_height, maturity))
        pruned = result.rowcount
        if pruned > 0:
            logger.debug(f"Pruned {pruned} matured coinbase UTXOs")
        return pruned

    def get_utxos_for_spending(self, address: str, target_value: int, min_conf: int = 1) -> List[Dict[str, Any]]:
        return self.db.fetch_all("""
            SELECT * FROM utxo
            WHERE address = ?
            ORDER BY value ASC
        """, (address,))

    def compact(self) -> None:
        self.db.execute("VACUUM")
        logger.info("UTXO store compacted")

    def verify_consistency(self) -> bool:
        duplicates = self.db.fetch_one("""
            SELECT outpoint, COUNT(*) as cnt
            FROM utxo
            GROUP BY outpoint
            HAVING cnt > 1
            LIMIT 1
        """)
        if duplicates:
            logger.error(f"Duplicate UTXO found: {duplicates}")
            return False
        negative = self.db.fetch_one("SELECT COUNT(*) as cnt FROM utxo WHERE value < 0")
        if negative and negative['cnt'] > 0:
            logger.error(f"Negative UTXO values found: {negative['cnt']}")
            return False
        return True
