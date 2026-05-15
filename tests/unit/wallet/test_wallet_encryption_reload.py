"""Wallet encrypted reload and passphrase failure tests."""

import os
import tempfile
import unittest
from pathlib import Path

from node.wallet.simple_wallet import SimpleWalletManager


class TestWalletEncryptionReload(unittest.TestCase):
    def test_encrypted_wallet_reload_with_correct_passphrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            m1 = SimpleWalletManager(
                datadir,
                network="regtest",
                wallet_passphrase="unit-test-passphrase",
            )
            w = m1.create_wallet()
            m1.activate_wallet(w.private_key_hex)

            m2 = SimpleWalletManager(
                datadir,
                network="regtest",
                wallet_passphrase="unit-test-passphrase",
            )
            loaded = m2.activate_wallet(w.private_key_hex)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.address, w.address)

    def test_wrong_passphrase_fails_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            m1 = SimpleWalletManager(
                datadir,
                network="regtest",
                wallet_passphrase="unit-test-passphrase",
            )
            w = m1.create_wallet()

            m2 = SimpleWalletManager(
                datadir,
                network="regtest",
                wallet_passphrase="unit-test-passphrase",
            )
            loaded = m2.activate_wallet(w.private_key_hex)
            self.assertIsNotNone(loaded)
            m2.lock_wallet()
            self.assertFalse(m2.wallet_passphrase("wrong-passphrase", 30))

    def test_mainnet_requires_explicit_passphrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            old_env = os.environ.pop("BERZCOIN_WALLET_PASSPHRASE", None)
            old_allow = os.environ.pop("BERZCOIN_ALLOW_INSECURE_WALLET_FALLBACK", None)
            try:
                with self.assertRaises(ValueError):
                    SimpleWalletManager(datadir, network="mainnet", wallet_passphrase="")
            finally:
                if old_env is not None:
                    os.environ["BERZCOIN_WALLET_PASSPHRASE"] = old_env
                if old_allow is not None:
                    os.environ["BERZCOIN_ALLOW_INSECURE_WALLET_FALLBACK"] = old_allow


if __name__ == "__main__":
    unittest.main()
