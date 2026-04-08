"""Regression tests for tx/address indexer runtime wiring."""

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


class _ChainStateStub:
    def get_best_height(self) -> int:
        return 0


def _coinbase_block_with_p2pkh_output(prev_hash: bytes) -> Block:
    pkh = bytes(range(1, 21))
    p2pkh_script = b"\x76\xa9\x14" + pkh + b"\x88\xac"
    tx = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=b"\x00" * 32,
                prev_tx_index=0xFFFFFFFF,
                script_sig=b"\x01\x01",
                sequence=0xFFFFFFFF,
            )
        ],
        outputs=[TxOut(value=50_00000000, script_pubkey=p2pkh_script)],
        locktime=0,
    )
    mr = merkle_root([tx.txid()]) or (b"\x00" * 32)
    header = BlockHeader(
        version=1,
        prev_block_hash=prev_hash,
        merkle_root=mr,
        timestamp=1_700_000_000,
        bits=0x207FFFFF,
        nonce=1,
    )
    return Block(header=header, transactions=[tx])


class TestIndexerWiring(unittest.TestCase):
    def _setup_node_with_db(self) -> tuple[BerzCoinNode, Database, tempfile.TemporaryDirectory]:
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name), "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()

        node = BerzCoinNode()
        node.db = db
        node.chainstate = _ChainStateStub()
        return node, db, tmp

    def test_init_indexers_respects_config(self) -> None:
        node, db, tmp = self._setup_node_with_db()
        try:
            node.config.set("txindex", False)
            node.config.set("addressindex", True)
            asyncio.run(node._init_indexers())
            self.assertIsNone(node.tx_indexer)
            self.assertIsNone(node.address_indexer)

            node.config.set("txindex", True)
            node.config.set("addressindex", True)
            asyncio.run(node._init_indexers())
            self.assertIsNotNone(node.tx_indexer)
            self.assertIsNotNone(node.address_indexer)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_connected_block_is_indexed_when_txindex_enabled(self) -> None:
        node, db, tmp = self._setup_node_with_db()
        try:
            node.config.set("txindex", True)
            node.config.set("addressindex", True)
            asyncio.run(node._init_indexers())

            block = _coinbase_block_with_p2pkh_output(b"\x11" * 32)
            node._index_connected_block(block, height=1)

            txid = block.transactions[0].txid().hex()
            tx_row = db.fetch_one("SELECT txid FROM tx_index WHERE txid = ?", (txid,))
            self.assertIsNotNone(tx_row)
            addr_rows = db.fetch_all("SELECT address FROM address_txs")
            self.assertEqual(len(addr_rows), 1)
            self.assertTrue(bool(addr_rows[0]["address"]))
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
