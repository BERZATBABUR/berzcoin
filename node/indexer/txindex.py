"""Transaction index for fast transaction lookup."""

import hashlib
from typing import Any, Dict, List, Optional

from shared.consensus.weights import calculate_transaction_weight
from shared.core.transaction import Transaction
from shared.crypto.bech32 import bech32_encode
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.storage.db import Database

logger = get_logger()

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58check_encode(version_and_payload: bytes) -> str:
    """Encode version byte + payload with double-SHA256 checksum (mainnet-style)."""
    chk = hashlib.sha256(hashlib.sha256(version_and_payload).digest()).digest()[:4]
    data = version_and_payload + chk
    n = int.from_bytes(data, "big")
    enc = ""
    while n > 0:
        n, r = divmod(n, 58)
        enc = _B58[r] + enc
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return (_B58[0] * pad) + enc if enc else (_B58[0] * pad)


class TransactionIndex:
    """Transaction index for fast lookup by txid."""

    def __init__(self, db: Database, chainstate: ChainState):
        self.db = db
        self.chainstate = chainstate
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_size = 1000

    def index_transaction(
        self,
        tx: Transaction,
        block_hash: str,
        height: int,
        block_time: int,
        block_tx_index: int,
    ) -> None:
        txid = tx.txid().hex()
        weight = calculate_transaction_weight(tx)
        serialized = tx.serialize(include_witness=True)
        size = len(serialized)

        with self.db.transaction():
            self.db.execute(
                """
                INSERT OR REPLACE INTO tx_index
                (txid, block_hash, height, block_time, block_tx_index, version, locktime, size, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    txid,
                    block_hash,
                    height,
                    block_time,
                    block_tx_index,
                    tx.version,
                    tx.locktime,
                    size,
                    weight,
                ),
            )

            for i, txin in enumerate(tx.vin):
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO tx_inputs
                    (txid, input_index, prev_txid, prev_vout, script_sig, sequence)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        txid,
                        i,
                        txin.prev_tx_hash.hex(),
                        txin.prev_tx_index,
                        txin.script_sig,
                        txin.sequence,
                    ),
                )

            for i, txout in enumerate(tx.vout):
                address = self._extract_address(txout.script_pubkey)
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO tx_outputs
                    (txid, output_index, value, script_pubkey, address)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (txid, i, txout.value, txout.script_pubkey, address),
                )

        self._update_cache(
            txid,
            {
                "txid": txid,
                "block_hash": block_hash,
                "height": height,
                "block_time": block_time,
                "block_tx_index": block_tx_index,
                "version": tx.version,
                "locktime": tx.locktime,
                "size": size,
                "weight": weight,
                "inputs": [],
                "outputs": [],
            },
        )
        logger.debug("Indexed transaction %s...", txid[:16])

    def get_transaction(self, txid: str) -> Optional[Dict[str, Any]]:
        if txid in self._cache:
            return self._cache[txid]

        result = self.db.fetch_one("SELECT * FROM tx_index WHERE txid = ?", (txid,))
        if not result:
            return None

        inputs = self.db.fetch_all(
            "SELECT * FROM tx_inputs WHERE txid = ? ORDER BY input_index", (txid,)
        )
        outputs = self.db.fetch_all(
            "SELECT * FROM tx_outputs WHERE txid = ? ORDER BY output_index", (txid,)
        )

        tx_info: Dict[str, Any] = {
            "txid": result["txid"],
            "block_hash": result["block_hash"],
            "height": result["height"],
            "block_time": result["block_time"],
            "block_tx_index": result["block_tx_index"],
            "version": result["version"],
            "locktime": result["locktime"],
            "size": result["size"],
            "weight": result["weight"],
            "inputs": inputs,
            "outputs": outputs,
        }
        self._update_cache(txid, tx_info)
        return tx_info

    def get_transaction_by_height(
        self, height: int, block_tx_index: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if block_tx_index is not None:
            row = self.db.fetch_one(
                "SELECT * FROM tx_index WHERE height = ? AND block_tx_index = ?",
                (height, block_tx_index),
            )
            return [row] if row else []

        return self.db.fetch_all(
            "SELECT * FROM tx_index WHERE height = ? ORDER BY block_tx_index", (height,)
        )

    def get_transactions_for_address(
        self, address: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT DISTINCT t.* FROM tx_index t
            JOIN tx_outputs o ON t.txid = o.txid
            WHERE o.address = ?
            ORDER BY t.height DESC, t.block_tx_index DESC
            LIMIT ? OFFSET ?
            """,
            (address, limit, offset),
        )

    def get_address_balance(self, address: str, min_conf: int = 1) -> int:
        best_height = self.chainstate.get_best_height()
        results = self.db.fetch_all(
            """
            SELECT o.value, t.height FROM tx_outputs o
            JOIN tx_index t ON o.txid = t.txid
            WHERE o.address = ?
            AND o.spent = 0
            AND NOT EXISTS (
                SELECT 1 FROM tx_inputs i
                WHERE i.prev_txid = o.txid AND i.prev_vout = o.output_index
            )
            """,
            (address,),
        )
        balance = 0
        for row in results:
            confirmations = best_height - row["height"] + 1
            if confirmations >= min_conf:
                balance += row["value"]
        return balance

    def get_unspent_outputs(
        self, address: str, min_conf: int = 1, max_utxos: int = 100
    ) -> List[Dict[str, Any]]:
        best_height = self.chainstate.get_best_height()
        results = self.db.fetch_all(
            """
            SELECT o.txid, o.output_index, o.value, o.script_pubkey, t.height
            FROM tx_outputs o
            JOIN tx_index t ON o.txid = t.txid
            WHERE o.address = ?
            AND o.spent = 0
            AND NOT EXISTS (
                SELECT 1 FROM tx_inputs i
                WHERE i.prev_txid = o.txid AND i.prev_vout = o.output_index
            )
            ORDER BY o.value ASC
            LIMIT ?
            """,
            (address, max_utxos),
        )
        utxos: List[Dict[str, Any]] = []
        for row in results:
            confirmations = best_height - row["height"] + 1
            if confirmations >= min_conf:
                utxos.append(
                    {
                        "txid": row["txid"],
                        "vout": row["output_index"],
                        "amount": row["value"],
                        "script_pubkey": row["script_pubkey"],
                        "confirmations": confirmations,
                    }
                )
        return utxos

    def mark_output_spent(self, txid: str, vout: int, spent_by_txid: str) -> None:
        self.db.execute(
            """
            UPDATE tx_outputs SET spent = 1, spent_by = ?
            WHERE txid = ? AND output_index = ?
            """,
            (spent_by_txid, txid, vout),
        )

    def get_transaction_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(*) as count FROM tx_index")
        return int(result["count"]) if result else 0

    def get_address_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(DISTINCT address) as count FROM tx_outputs")
        return int(result["count"]) if result else 0

    def _extract_address(self, script_pubkey: bytes) -> str:
        if not script_pubkey:
            return ""
        # P2PKH
        if len(script_pubkey) == 25 and script_pubkey[0] == 0x76 and script_pubkey[1] == 0xA9:
            pubkey_hash = script_pubkey[3:23]
            return _base58check_encode(b"\x00" + pubkey_hash)
        # P2SH
        if len(script_pubkey) == 23 and script_pubkey[0] == 0xA9:
            script_hash = script_pubkey[2:22]
            return _base58check_encode(b"\x05" + script_hash)
        # P2WPKH
        if len(script_pubkey) == 22 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x14:
            witness_program = script_pubkey[2:22]
            net = (self.db.network or "mainnet").lower()
            hrp = "bc"
            if net == "testnet":
                hrp = "tb"
            elif net == "regtest":
                hrp = "bcrt"
            return bech32_encode(hrp, 0, witness_program)
        return ""

    def _update_cache(self, txid: str, tx_info: Dict[str, Any]) -> None:
        self._cache[txid] = tx_info
        if len(self._cache) > self._cache_size:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_transactions": self.get_transaction_count(),
            "total_addresses": self.get_address_count(),
            "cache_size": len(self._cache),
            "cached_transactions": len(self._cache),
        }
