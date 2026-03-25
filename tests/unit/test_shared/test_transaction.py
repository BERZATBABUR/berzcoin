"""Unit tests for transaction components."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.core.transaction import Transaction, TxIn, TxOut


class TestTransaction(unittest.TestCase):
    """Test transaction functionality."""

    def test_txin_creation(self) -> None:
        txin = TxIn(
            prev_tx_hash=b"\x01" * 32,
            prev_tx_index=0,
            script_sig=b"\x00",
            sequence=0xFFFFFFFF,
        )
        self.assertEqual(txin.prev_tx_hash, b"\x01" * 32)
        self.assertEqual(txin.prev_tx_index, 0)
        self.assertEqual(txin.sequence, 0xFFFFFFFF)
        self.assertFalse(txin.is_coinbase())

    def test_coinbase_txin(self) -> None:
        txin = TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=b"\x03\x01\x02\x03",
            sequence=0xFFFFFFFF,
        )
        self.assertTrue(txin.is_coinbase())

    def test_txout_creation(self) -> None:
        txout = TxOut(
            value=5000000000,
            script_pubkey=b"\x76\xa9\x14" + b"\x01" * 20 + b"\x88\xac",
        )
        self.assertEqual(txout.value, 5000000000)
        self.assertIsInstance(txout.script_pubkey, bytes)

    def test_transaction_creation(self) -> None:
        tx = Transaction(version=2)
        txin = TxIn(
            prev_tx_hash=b"\x01" * 32,
            prev_tx_index=0,
            script_sig=b"",
            sequence=0xFFFFFFFF,
        )
        tx.vin.append(txin)
        txout = TxOut(
            value=100000000,
            script_pubkey=b"\x76\xa9\x14" + b"\x01" * 20 + b"\x88\xac",
        )
        tx.vout.append(txout)
        self.assertEqual(len(tx.vin), 1)
        self.assertEqual(len(tx.vout), 1)
        self.assertFalse(tx.is_coinbase())

    def test_transaction_serialization(self) -> None:
        tx = Transaction(version=2)
        txin = TxIn(
            prev_tx_hash=b"\x01" * 32,
            prev_tx_index=0,
            script_sig=b"",
            sequence=0xFFFFFFFF,
        )
        tx.vin.append(txin)
        txout = TxOut(
            value=100000000,
            script_pubkey=b"\x76\xa9\x14" + b"\x01" * 20 + b"\x88\xac",
        )
        tx.vout.append(txout)
        serialized = tx.serialize(include_witness=False)
        self.assertTrue(len(serialized) > 0)
        recovered, _ = Transaction.deserialize(serialized)
        self.assertEqual(len(recovered.vin), len(tx.vin))
        self.assertEqual(len(recovered.vout), len(tx.vout))
        self.assertEqual(recovered.version, tx.version)

    def test_txid_calculation(self) -> None:
        tx = Transaction(version=1)
        txin = TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=b"\x01\x02\x03",
            sequence=0xFFFFFFFF,
        )
        tx.vin.append(txin)
        txout = TxOut(
            value=5000000000,
            script_pubkey=b"\x76\xa9\x14" + b"\x01" * 20 + b"\x88\xac",
        )
        tx.vout.append(txout)
        txid = tx.txid()
        self.assertEqual(len(txid), 32)
        self.assertIsInstance(txid, bytes)


if __name__ == "__main__":
    unittest.main()
