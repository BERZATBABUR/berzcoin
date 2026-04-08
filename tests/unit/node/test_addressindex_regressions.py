"""Regression tests for AddressIndex UTXO queries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from node.indexer.addressindex import AddressIndex
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations


def _open_migrated_db(data_dir: Path, network: str = "regtest") -> Database:
    db = Database(data_dir, network)
    db.connect()
    migrations = Migrations(db)
    register_standard_migrations(migrations)
    migrations.migrate()
    return db


class TestAddressIndexRegressions(unittest.TestCase):
    def test_get_address_utxos_excludes_outputs_spent_by_tx_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _open_migrated_db(Path(tmpdir))
            try:
                addr = "bcrt1qexampleaddress0000000000000000000000000"

                db.execute(
                    """
                    INSERT INTO tx_index
                    (txid, block_hash, height, block_time, block_tx_index, version, locktime, size, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("fundingtx", "block1", 1, 1, 0, 2, 0, 100, 400),
                )
                db.execute(
                    """
                    INSERT INTO tx_outputs
                    (txid, output_index, value, script_pubkey, address, spent)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("fundingtx", 0, 5000, b"\x51", addr, 0),
                )
                # Simulate spending via tx_inputs table while tx_outputs.spent is stale.
                db.execute(
                    """
                    INSERT INTO tx_inputs
                    (txid, input_index, prev_txid, prev_vout, script_sig, sequence)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("spendtx", 0, "fundingtx", 0, b"", 0),
                )

                index = AddressIndex(db)
                utxos = index.get_address_utxos(addr, min_conf=1, best_height=2)
                self.assertEqual(utxos, [])
            finally:
                db.disconnect()

    def test_get_address_utxos_reports_one_confirmation_at_height_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _open_migrated_db(Path(tmpdir))
            try:
                addr = "bcrt1qconfirm000000000000000000000000000000000"
                db.execute(
                    """
                    INSERT INTO tx_index
                    (txid, block_hash, height, block_time, block_tx_index, version, locktime, size, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("genesisout", "block0", 0, 1, 0, 2, 0, 100, 400),
                )
                db.execute(
                    """
                    INSERT INTO tx_outputs
                    (txid, output_index, value, script_pubkey, address, spent)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("genesisout", 0, 7000, b"\x51", addr, 0),
                )

                index = AddressIndex(db)
                utxos = index.get_address_utxos(addr, min_conf=1, best_height=0)
                self.assertEqual(len(utxos), 1)
                self.assertEqual(int(utxos[0]["confirmations"]), 1)
            finally:
                db.disconnect()


if __name__ == "__main__":
    unittest.main()

