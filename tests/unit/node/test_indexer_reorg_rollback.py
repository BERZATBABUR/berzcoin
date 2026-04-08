"""Regression tests for tx/address index reconciliation after reorg."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.app.main import BerzCoinNode
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


class _Entry:
    def __init__(self, height: int):
        self.height = int(height)


class _BlockIndexStub:
    def __init__(self):
        self._entries = {}

    def set_height(self, block_hash: str, height: int) -> None:
        self._entries[block_hash] = _Entry(height)

    def get_block(self, block_hash: str):
        return self._entries.get(block_hash)


class _ChainStateStub:
    def __init__(self):
        self.block_index = _BlockIndexStub()


def _p2pkh_script(seed: int) -> bytes:
    payload = bytes(((seed + i) % 256 for i in range(20)))
    return b"\x76\xa9\x14" + payload + b"\x88\xac"


def _coinbase(prev_hash: bytes, nonce: int, seed: int) -> Block:
    tx = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=b"\x00" * 32,
                prev_tx_index=0xFFFFFFFF,
                script_sig=bytes([seed]),
                sequence=0xFFFFFFFF,
            )
        ],
        outputs=[TxOut(value=50_00000000, script_pubkey=_p2pkh_script(seed))],
        locktime=0,
    )
    mr = merkle_root([tx.txid()]) or (b"\x00" * 32)
    header = BlockHeader(
        version=1,
        prev_block_hash=prev_hash,
        merkle_root=mr,
        timestamp=1_700_000_000 + nonce,
        bits=0x207FFFFF,
        nonce=nonce,
    )
    return Block(header=header, transactions=[tx])


def _spend(prev_hash: bytes, nonce: int, spend_tx: Transaction, seed: int) -> Block:
    tx = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=spend_tx.txid(),
                prev_tx_index=0,
                script_sig=b"\x51",
                sequence=0xFFFFFFFE,
            )
        ],
        outputs=[TxOut(value=49_99990000, script_pubkey=_p2pkh_script(seed))],
        locktime=0,
    )
    mr = merkle_root([tx.txid()]) or (b"\x00" * 32)
    header = BlockHeader(
        version=1,
        prev_block_hash=prev_hash,
        merkle_root=mr,
        timestamp=1_700_001_000 + nonce,
        bits=0x207FFFFF,
        nonce=nonce,
    )
    return Block(header=header, transactions=[tx])


class TestIndexerReorgRollback(unittest.TestCase):
    def _setup_node(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name), "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()

        node = BerzCoinNode()
        node.db = db
        node.chainstate = _ChainStateStub()
        node.config.set("txindex", True)
        node.config.set("addressindex", True)
        asyncio.run(node._init_indexers())
        return tmp, db, node

    def test_reconcile_indexes_replaces_disconnected_branch_rows(self) -> None:
        tmp, db, node = self._setup_node()
        try:
            old_block = _coinbase(b"\x11" * 32, nonce=1, seed=10)
            new_block = _coinbase(b"\x11" * 32, nonce=2, seed=20)
            node.chainstate.block_index.set_height(old_block.header.hash_hex(), 1)
            node.chainstate.block_index.set_height(new_block.header.hash_hex(), 1)

            node._index_connected_block(old_block, 1)
            old_txid = old_block.transactions[0].txid().hex()
            self.assertIsNotNone(db.fetch_one("SELECT txid FROM tx_index WHERE txid = ?", (old_txid,)))

            node._reconcile_indexes_after_reorg([old_block], [new_block])

            new_txid = new_block.transactions[0].txid().hex()
            self.assertIsNone(db.fetch_one("SELECT txid FROM tx_index WHERE txid = ?", (old_txid,)))
            self.assertIsNotNone(db.fetch_one("SELECT txid FROM tx_index WHERE txid = ?", (new_txid,)))

            rows = db.fetch_all("SELECT DISTINCT address FROM address_txs")
            self.assertEqual(len(rows), 1)
            self.assertTrue(bool(rows[0]["address"]))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_disconnect_reconciliation_restores_spent_flags(self) -> None:
        tmp, db, node = self._setup_node()
        try:
            block1 = _coinbase(b"\x22" * 32, nonce=3, seed=30)
            block2 = _spend(block1.header.hash(), nonce=4, spend_tx=block1.transactions[0], seed=31)
            node.chainstate.block_index.set_height(block1.header.hash_hex(), 1)
            node.chainstate.block_index.set_height(block2.header.hash_hex(), 2)

            node._index_connected_block(block1, 1)
            node._index_connected_block(block2, 2)

            prev_txid = block1.transactions[0].txid().hex()
            spent_row = db.fetch_one(
                "SELECT spent FROM tx_outputs WHERE txid = ? AND output_index = 0",
                (prev_txid,),
            )
            self.assertEqual(int(spent_row["spent"]), 1)

            node._reconcile_indexes_after_reorg([block2], [])

            unspent_row = db.fetch_one(
                "SELECT spent FROM tx_outputs WHERE txid = ? AND output_index = 0",
                (prev_txid,),
            )
            self.assertEqual(int(unspent_row["spent"]), 0)
            self.assertIsNone(
                db.fetch_one("SELECT txid FROM tx_index WHERE txid = ?", (block2.transactions[0].txid().hex(),))
            )
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
