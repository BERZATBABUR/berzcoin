"""Unit tests for simple-wallet private-key normalization and activation."""

import tempfile
import time
import unittest
from pathlib import Path

from node.wallet.simple_wallet import SimpleWallet, SimpleWalletManager


class TestSimpleWalletKeys(unittest.TestCase):
    def test_from_private_key_normalizes_0x_and_padding(self) -> None:
        wallet = SimpleWallet.from_private_key("0x1")
        self.assertEqual(wallet.private_key_hex, "0" * 63 + "1")
        self.assertEqual(len(wallet.private_key_hex), 64)

    def test_manager_rejects_invalid_private_key_hex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SimpleWalletManager(Path(tmp))
            with self.assertRaises(ValueError):
                manager.activate_wallet("not-hex")

    def test_manager_rejects_out_of_range_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SimpleWalletManager(Path(tmp))
            with self.assertRaises(ValueError):
                manager.activate_wallet("0")

    def test_same_private_key_produces_same_wallet_identity(self) -> None:
        key = "0x1"
        w1 = SimpleWallet.from_private_key(key, network="regtest")
        w2 = SimpleWallet.from_private_key(key, network="regtest")
        self.assertEqual(w1.private_key_hex, w2.private_key_hex)
        self.assertEqual(w1.public_key_hex, w2.public_key_hex)
        self.assertEqual(w1.address, w2.address)
        self.assertEqual(w1.mnemonic, w2.mnemonic)

    def test_auto_lock_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SimpleWalletManager(
                Path(tmp),
                network="regtest",
                wallet_passphrase="unit-test-passphrase",
                default_unlock_timeout_secs=1,
            )
            wallet = manager.create_wallet()
            manager.activate_wallet(wallet.private_key_hex)
            self.assertTrue(manager.is_wallet_unlocked())
            time.sleep(1.2)
            self.assertFalse(manager.is_wallet_unlocked())
            self.assertIsNone(manager.get_active_private_key())


if __name__ == "__main__":
    unittest.main()
