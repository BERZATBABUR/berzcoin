"""Address index for fast address lookups."""

import time
from typing import Any, Dict, List

from shared.utils.logging import get_logger
from node.storage.db import Database

logger = get_logger()


class AddressIndex:
    """Address index for address-based queries."""

    def __init__(self, db: Database):
        self.db = db
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_size = 1000
        self._init_tables()

    def _init_tables(self) -> None:
        with self.db.transaction():
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS address_txs (
                    address TEXT NOT NULL,
                    txid TEXT NOT NULL,
                    height INTEGER NOT NULL,
                    block_time INTEGER NOT NULL,
                    block_tx_index INTEGER NOT NULL,
                    is_input BOOLEAN NOT NULL,
                    is_output BOOLEAN NOT NULL,
                    io_index INTEGER NOT NULL,
                    value INTEGER DEFAULT 0,
                    PRIMARY KEY (address, txid, is_input, io_index)
                )
                """
            )
            self.db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_address_txs_height
                ON address_txs(address, height DESC)
                """
            )
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS address_balance (
                    address TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL,
                    unconfirmed_balance INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            self.db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_address_balance
                ON address_balance(balance DESC)
                """
            )

    def index_address(
        self,
        address: str,
        txid: str,
        height: int,
        block_time: int,
        block_tx_index: int,
        is_input: bool,
        is_output: bool,
        io_index: int,
        value: int = 0,
    ) -> None:
        if not address:
            return
        self.db.execute(
            """
            INSERT OR REPLACE INTO address_txs
            (address, txid, height, block_time, block_tx_index, is_input, is_output, io_index, value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                address,
                txid,
                height,
                block_time,
                block_tx_index,
                is_input,
                is_output,
                io_index,
                value,
            ),
        )

        if is_output:
            self._update_balance(address, value, 0)
        elif is_input:
            self._update_balance(address, -value, 0)

        self._cache.pop(address, None)

    def _update_balance(self, address: str, delta: int, unconfirmed_delta: int) -> None:
        now = int(time.time())
        self.db.execute(
            """
            INSERT INTO address_balance (address, balance, unconfirmed_balance, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                balance = balance + ?,
                unconfirmed_balance = unconfirmed_balance + ?,
                updated_at = ?
            """,
            (address, delta, unconfirmed_delta, now, delta, unconfirmed_delta, now),
        )

    def get_address_info(self, address: str) -> Dict[str, Any]:
        if address in self._cache:
            return self._cache[address]

        balance_info = self.db.fetch_one(
            """
            SELECT balance, unconfirmed_balance FROM address_balance
            WHERE address = ?
            """,
            (address,),
        )
        tx_count = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM address_txs WHERE address = ?", (address,)
        )
        first_seen = self.db.fetch_one(
            """
            SELECT MIN(height) as first_height, MIN(block_time) as first_time
            FROM address_txs WHERE address = ?
            """,
            (address,),
        )

        info: Dict[str, Any] = {
            "address": address,
            "balance": balance_info["balance"] if balance_info else 0,
            "unconfirmed_balance": balance_info["unconfirmed_balance"] if balance_info else 0,
            "total_transactions": tx_count["count"] if tx_count else 0,
            "first_seen_height": first_seen["first_height"] if first_seen else None,
            "first_seen_time": first_seen["first_time"] if first_seen else None,
        }
        self._update_cache(address, info)
        return info

    def get_address_transactions(
        self, address: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT txid, height, block_time, block_tx_index, is_input, is_output, io_index, value
            FROM address_txs
            WHERE address = ?
            ORDER BY height DESC, block_time DESC, block_tx_index DESC, io_index DESC
            LIMIT ? OFFSET ?
            """,
            (address, limit, offset),
        )

    def get_address_history(self, address: str) -> List[Dict[str, Any]]:
        results = self.db.fetch_all(
            """
            SELECT txid, height, block_time, block_tx_index, is_input, is_output, io_index, value
            FROM address_txs
            WHERE address = ?
            ORDER BY height ASC, block_tx_index ASC, io_index ASC
            """,
            (address,),
        )
        history: List[Dict[str, Any]] = []
        running_balance = 0
        for row in results:
            if row["is_output"]:
                running_balance += row["value"]
                history.append(
                    {
                        "txid": row["txid"],
                        "height": row["height"],
                        "time": row["block_time"],
                        "type": "receive",
                        "amount": row["value"],
                        "balance": running_balance,
                    }
                )
            elif row["is_input"]:
                running_balance -= row["value"]
                history.append(
                    {
                        "txid": row["txid"],
                        "height": row["height"],
                        "time": row["block_time"],
                        "type": "send",
                        "amount": -row["value"],
                        "balance": running_balance,
                    }
                )
        return history

    def get_address_utxos(
        self, address: str, min_conf: int = 1, best_height: int = 0
    ) -> List[Dict[str, Any]]:
        results = self.db.fetch_all(
            """
            SELECT o.txid, o.output_index, o.value, o.script_pubkey,
                   tx.height, tx.block_time
            FROM tx_outputs o
            JOIN tx_index tx ON o.txid = tx.txid
            WHERE o.address = ? AND o.spent = 0
            AND NOT EXISTS (
                SELECT 1 FROM tx_inputs i
                WHERE i.prev_txid = o.txid AND i.prev_vout = o.output_index
            )
            ORDER BY o.value ASC
            """,
            (address,),
        )
        utxos: List[Dict[str, Any]] = []
        for row in results:
            confirmations = best_height - row["height"] + 1 if best_height >= row["height"] else 0
            if confirmations >= min_conf:
                utxos.append(
                    {
                        "txid": row["txid"],
                        "vout": row["output_index"],
                        "amount": row["value"],
                        "script_pubkey": row["script_pubkey"],
                        "height": row["height"],
                        "confirmations": confirmations,
                    }
                )
        return utxos

    def get_top_addresses(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT address, balance, unconfirmed_balance
            FROM address_balance
            WHERE balance > 0
            ORDER BY balance DESC
            LIMIT ?
            """,
            (limit,),
        )

    def get_address_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(*) as count FROM address_balance")
        return int(result["count"]) if result else 0

    def get_active_addresses(self, since_height: int) -> int:
        result = self.db.fetch_one(
            """
            SELECT COUNT(DISTINCT address) as count
            FROM address_txs
            WHERE height >= ?
            """,
            (since_height,),
        )
        return int(result["count"]) if result else 0

    def reindex_address(self, address: str) -> None:
        self.db.execute("DELETE FROM address_txs WHERE address = ?", (address,))
        self.db.execute("DELETE FROM address_balance WHERE address = ?", (address,))

        outputs = self.db.fetch_all(
            """
            SELECT txid, output_index, value, address
            FROM tx_outputs WHERE address = ?
            """,
            (address,),
        )
        for output in outputs:
            tx_info = self.db.fetch_one(
                "SELECT height, block_time, block_tx_index FROM tx_index WHERE txid = ?",
                (output["txid"],),
            )
            if tx_info:
                self.index_address(
                    address,
                    output["txid"],
                    tx_info["height"],
                    tx_info["block_time"],
                    tx_info["block_tx_index"],
                    False,
                    True,
                    output["output_index"],
                    output["value"],
                )

        inputs = self.db.fetch_all(
            """
            SELECT i.txid, i.input_index, i.prev_txid, i.prev_vout, t.height, t.block_time,
                   t.block_tx_index, o.value
            FROM tx_inputs i
            JOIN tx_outputs o ON i.prev_txid = o.txid AND i.prev_vout = o.output_index
            JOIN tx_index t ON i.txid = t.txid
            WHERE o.address = ?
            """,
            (address,),
        )
        for inp in inputs:
            self.index_address(
                address,
                inp["txid"],
                inp["height"],
                inp["block_time"],
                inp["block_tx_index"],
                True,
                False,
                inp["input_index"],
                inp["value"] or 0,
            )

        logger.info("Reindexed address %s...", address[:16])

    def rebuild_from_tx_index(self) -> None:
        """Rebuild address index tables from canonical tx_index/tx_inputs/tx_outputs tables."""
        now = int(time.time())
        with self.db.transaction():
            self.db.execute("DELETE FROM address_txs")
            self.db.execute("DELETE FROM address_balance")

            self.db.execute(
                """
                INSERT INTO address_txs
                (address, txid, height, block_time, block_tx_index, is_input, is_output, io_index, value)
                SELECT o.address, o.txid, t.height, t.block_time, t.block_tx_index,
                       0, 1, o.output_index, o.value
                FROM tx_outputs o
                JOIN tx_index t ON t.txid = o.txid
                WHERE o.address IS NOT NULL AND o.address != ''
                """
            )

            self.db.execute(
                """
                INSERT INTO address_txs
                (address, txid, height, block_time, block_tx_index, is_input, is_output, io_index, value)
                SELECT o.address, i.txid, t.height, t.block_time, t.block_tx_index,
                       1, 0, i.input_index, COALESCE(o.value, 0)
                FROM tx_inputs i
                JOIN tx_index t ON t.txid = i.txid
                JOIN tx_outputs o
                  ON o.txid = i.prev_txid AND o.output_index = i.prev_vout
                WHERE o.address IS NOT NULL AND o.address != ''
                """
            )

            self.db.execute(
                """
                INSERT INTO address_balance (address, balance, unconfirmed_balance, updated_at)
                SELECT
                    address,
                    COALESCE(SUM(CASE WHEN is_output = 1 THEN value ELSE -value END), 0) AS balance,
                    0,
                    ?
                FROM address_txs
                GROUP BY address
                """,
                (now,),
            )
        self._cache.clear()

    def _update_cache(self, address: str, info: Dict[str, Any]) -> None:
        self._cache[address] = info
        if len(self._cache) > self._cache_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_addresses": self.get_address_count(),
            "cache_size": len(self._cache),
            "cached_addresses": len(self._cache),
        }
