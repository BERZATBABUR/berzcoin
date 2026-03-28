"""Simple-wallet parity regressions: descriptors, change handling, and backups."""

import json
import os
import shutil
import tempfile
import unittest

from node.wallet.core.coin_selection import CoinSelector
from node.wallet.core.keystore import KeyStore
from node.wallet.simple_wallet import SimpleWallet
from node.wallet.storage.backup import WalletBackup


class TestWalletParity(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.temp_dir, "wallet.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_keystore_exports_descriptor_like_metadata(self) -> None:
        ks = KeyStore(self.wallet_path, network="regtest")
        mnemonic = ks.create_master_key()
        self.assertTrue(bool(mnemonic))

        desc = ks.export_descriptors()
        self.assertEqual(len(desc), 2)
        self.assertTrue(any(d["internal"] for d in desc))
        self.assertTrue(any("wpkh(" in d["descriptor"] for d in desc))

    def test_change_address_comes_from_internal_chain(self) -> None:
        ks = KeyStore(self.wallet_path, network="regtest")
        ks.create_master_key()

        change = ks.get_change_address()
        self.assertIsNotNone(change)
        key = ks.get_key(change or "")
        self.assertIsNotNone(key)
        self.assertTrue(bool(key and key.is_internal))

    def test_coin_selector_accepts_dict_utxos(self) -> None:
        selector = CoinSelector()
        utxos = [
            {"txid": "aa" * 32, "vout": 0, "amount": 5000},
            {"txid": "bb" * 32, "vout": 1, "amount": 7000},
        ]
        res = selector.select_coins(utxos, target=6000, fee_rate=1, strategy="optimal")
        self.assertIsNotNone(res)
        assert res is not None
        self.assertGreaterEqual(res.total_selected, 6000)
        self.assertGreaterEqual(res.fee, 0)

    def test_backup_network_mismatch_is_rejected(self) -> None:
        wallet = SimpleWallet.create(network="regtest")
        with open(self.wallet_path, "w", encoding="utf-8") as f:
            json.dump(wallet.to_dict(), f)

        mgr = WalletBackup(self.wallet_path)
        created = mgr.create_backup("network_guard", network="regtest")
        self.assertTrue(created)
        self.assertFalse(mgr.restore_backup("network_guard", expected_network="mainnet"))


if __name__ == "__main__":
    unittest.main()

