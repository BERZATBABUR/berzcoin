"""Tests for wallet metadata index store."""

import tempfile
import unittest
from pathlib import Path

from node.wallet.storage.multiwallet import MultiWalletStore


class TestMultiWalletStore(unittest.TestCase):
    def test_upsert_and_list_wallets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MultiWalletStore(Path(tmp))
            store.upsert_wallet("addr1", network="regtest")
            store.upsert_wallet("addr2", network="regtest", label="miner")

            wallets = store.list_wallets(network="regtest")
            self.assertEqual([w.address for w in wallets], ["addr1", "addr2"])
            self.assertEqual(wallets[1].label, "miner")

    def test_default_wallet_persists_across_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            store = MultiWalletStore(data_dir)
            store.upsert_wallet("addr-default", network="mainnet")
            self.assertTrue(store.set_default_wallet("addr-default"))

            reloaded = MultiWalletStore(data_dir)
            default = reloaded.get_default_wallet(network="mainnet")
            self.assertIsNotNone(default)
            self.assertEqual(default.address, "addr-default")

    def test_refresh_from_disk_adds_missing_wallet_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            wallets_dir = data_dir / "wallets"
            wallets_dir.mkdir(parents=True, exist_ok=True)
            (wallets_dir / "addr-file-only.json").write_text("{}", encoding="utf-8")

            store = MultiWalletStore(data_dir)
            added = store.refresh_from_disk(network="regtest")

            self.assertEqual(added, 1)
            wallet = store.get_wallet("addr-file-only")
            self.assertIsNotNone(wallet)
            self.assertEqual(wallet.network, "regtest")


if __name__ == "__main__":
    unittest.main()
