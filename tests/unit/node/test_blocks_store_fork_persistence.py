"""Regression tests for fork block persistence at same height."""

import tempfile
import unittest
from pathlib import Path

from node.storage.blocks_store import BlocksStore
from node.storage.db import Database
from node.storage.schema import Schema
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


def _coinbase(tag: bytes) -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [
        TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=tag,
            sequence=0xFFFFFFFF,
        )
    ]
    tx.vout = [TxOut(value=0, script_pubkey=b"")]
    return tx


def _block(prev_hash: bytes, nonce: int, tag: bytes) -> Block:
    tx = _coinbase(tag)
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


class TestBlocksStoreForkPersistence(unittest.TestCase):
    def test_same_height_blocks_persist_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = Database(data_dir, "regtest")
            db.connect()
            try:
                schema = Schema(db)
                schema.create_tables()
                schema.create_indexes()

                store = BlocksStore(db, data_dir)
                prev = b"\x11" * 32
                b1 = _block(prev, nonce=1, tag=b"\x02\x01")
                b2 = _block(prev, nonce=2, tag=b"\x02\x02")

                self.assertNotEqual(b1.header.hash_hex(), b2.header.hash_hex())
                store.write_block(b1, height=1)
                store.write_block(b2, height=1)

                # Both block rows should exist at the same height.
                rows = db.fetch_all("SELECT hash FROM blocks WHERE height = ?", (1,))
                self.assertEqual(len(rows), 2)
                hashes = {r["hash"] for r in rows}
                self.assertIn(b1.header.hash_hex(), hashes)
                self.assertIn(b2.header.hash_hex(), hashes)

                # Hash-keyed files/read path should return each distinct block.
                r1 = store.read_block_by_hash(b1.header.hash_hex())
                r2 = store.read_block_by_hash(b2.header.hash_hex())
                self.assertIsNotNone(r1)
                self.assertIsNotNone(r2)
                self.assertEqual(r1.header.hash_hex(), b1.header.hash_hex())
                self.assertEqual(r2.header.hash_hex(), b2.header.hash_hex())

                tx_row = db.fetch_one(
                    "SELECT weight FROM transactions WHERE txid = ?",
                    (b1.transactions[0].txid().hex(),),
                )
                self.assertIsNotNone(tx_row)
                self.assertEqual(int(tx_row["weight"]), b1.transactions[0].weight())
            finally:
                db.disconnect()


if __name__ == "__main__":
    unittest.main()
