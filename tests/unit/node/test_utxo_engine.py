"""Regression tests for UTXO add/spend/remove bookkeeping."""

import tempfile
import unittest
from pathlib import Path

from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore


class TestUTXOEngine(unittest.TestCase):
    def _setup_db(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name), "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return tmp, db

    def _insert_tx_context(self, db: Database, txid: str, block_hash: str, height: int = 1) -> None:
        db.execute(
            """
            INSERT INTO blocks
            (height, hash, version, prev_block_hash, merkle_root, timestamp, bits, nonce,
             tx_count, size, weight, is_valid, processed_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, 1, 80, 320, 1, ?)
            """,
            (height, block_hash, "00" * 32, "11" * 32, 1, 0x207FFFFF, 0, 1),
        )
        db.execute(
            """
            INSERT INTO transactions
            (txid, block_hash, height, "index", version, locktime, size, weight, is_coinbase)
            VALUES (?, ?, ?, 0, 1, 0, 100, 400, 0)
            """,
            (txid, block_hash, height),
        )

    def _insert_block_row(self, db: Database, block_hash: str, height: int) -> None:
        db.execute(
            """
            INSERT INTO blocks
            (height, hash, version, prev_block_hash, merkle_root, timestamp, bits, nonce,
             tx_count, size, weight, is_valid, processed_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, 0, 80, 320, 1, ?)
            """,
            (height, block_hash, "00" * 32, "11" * 32, 1, 0x207FFFFF, 0, 1),
        )

    def test_spend_records_spender_txid_and_index(self) -> None:
        tmp, db = self._setup_db()
        try:
            store = UTXOStore(db)
            src_txid = "aa" * 32
            self._insert_tx_context(db, src_txid, "10" * 32)
            db.execute(
                """
                INSERT INTO outputs (txid, "index", value, script_pubkey, spent)
                VALUES (?, ?, ?, ?, 0)
                """,
                (src_txid, 0, 1000, b"\x51"),
            )
            store.add_utxo(src_txid, 0, 1000, b"\x51", 1, False)

            self.assertTrue(
                store.spend_utxo(
                    src_txid,
                    0,
                    spent_by_txid="bb" * 32,
                    spent_by_index=1,
                )
            )
            self.assertIsNone(store.get_utxo(src_txid, 0))
            row = db.fetch_one(
                'SELECT spent, spent_by_txid, spent_by_index FROM outputs WHERE txid = ? AND "index" = 0',
                (src_txid,),
            )
            self.assertEqual(row["spent"], 1)
            self.assertEqual(row["spent_by_txid"], "bb" * 32)
            self.assertEqual(row["spent_by_index"], 1)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_remove_utxo_does_not_mark_output_spent(self) -> None:
        tmp, db = self._setup_db()
        try:
            store = UTXOStore(db)
            txid = "cc" * 32
            self._insert_tx_context(db, txid, "20" * 32)
            db.execute(
                """
                INSERT INTO outputs (txid, "index", value, script_pubkey, spent)
                VALUES (?, ?, ?, ?, 0)
                """,
                (txid, 0, 500, b"\x51"),
            )
            store.add_utxo(txid, 0, 500, b"\x51", 2, False)

            self.assertTrue(store.remove_utxo(txid, 0))
            row = db.fetch_one(
                'SELECT spent, spent_by_txid, spent_by_index FROM outputs WHERE txid = ? AND "index" = 0',
                (txid,),
            )
            self.assertEqual(row["spent"], 0)
            self.assertIsNone(row["spent_by_txid"])
            self.assertIsNone(row["spent_by_index"])
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_prune_coinbase_does_not_delete_mature_spendable_outputs(self) -> None:
        tmp, db = self._setup_db()
        try:
            store = UTXOStore(db)
            txid = "dd" * 32
            db.execute(
                """
                INSERT INTO utxo
                (outpoint, txid, "index", value, script_pubkey, address, height, is_coinbase)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"{txid}:0", txid, 0, 1250, b"\x51", "bc1qexample", 10, 1),
            )

            pruned = store.prune_coinbase_utxos(current_height=200, maturity=100)
            self.assertEqual(pruned, 0)
            self.assertIsNotNone(store.get_utxo(txid, 0))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_get_utxos_for_spending_respects_target_and_min_confirmations(self) -> None:
        tmp, db = self._setup_db()
        try:
            store = UTXOStore(db)
            self._insert_block_row(db, "10" * 32, 1)
            self._insert_block_row(db, "11" * 32, 5)

            rows = [
                ("aa" * 32, 0, 3, 1),
                ("bb" * 32, 0, 4, 1),
                ("cc" * 32, 0, 9, 5),
            ]
            for txid, idx, value, height in rows:
                db.execute(
                    """
                    INSERT INTO utxo
                    (outpoint, txid, "index", value, script_pubkey, address, height, is_coinbase)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (f"{txid}:{idx}", txid, idx, value, b"\x51", "bc1qtarget", height),
                )

            selected = store.get_utxos_for_spending("bc1qtarget", target_value=7, min_conf=2)
            self.assertEqual([(u["txid"], u["value"]) for u in selected], [("aa" * 32, 3), ("bb" * 32, 4)])

            selected_with_low_conf = store.get_utxos_for_spending("bc1qtarget", target_value=7, min_conf=1)
            self.assertEqual(
                [(u["txid"], u["value"]) for u in selected_with_low_conf],
                [("aa" * 32, 3), ("bb" * 32, 4)],
            )
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
