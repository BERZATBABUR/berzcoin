"""Regression tests for private-key wallet activation flow."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.wallet import WalletHandlers


class _Config:
    def __init__(self, datadir: Path):
        self._datadir = datadir
        self._values = {
            "network": "regtest",
            "wallet_debug_secrets": False,
            "debug": False,
            "wallet_encryption_passphrase": "unit-test-passphrase",
            "wallet_default_unlock_timeout": 300,
        }

    def get_datadir(self) -> Path:
        return self._datadir

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value) -> None:
        self._values[key] = value


class _ChainState:
    def get_balance(self, _address: str) -> int:
        return 0

    def get_utxos_for_address(self, _address: str, _limit: int = 1000):
        return []


class _Node:
    def __init__(self, datadir: Path):
        self.config = _Config(datadir)
        self.chainstate = _ChainState()
        self.simple_wallet_manager = None
        self.mempool = None


class TestWalletPrivateKeyActivation(unittest.TestCase):
    def test_create_activate_roundtrip(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)

                created = await handlers.create_wallet()
                self.assertIn("private_key", created)
                private_key = created["private_key"]
                wallet_file = Path(tmp) / "wallets" / f"{created['address']}.json"
                on_disk = json.loads(wallet_file.read_text(encoding="utf-8"))
                self.assertEqual(on_disk.get("format"), "berzcoin.wallet.encrypted.v1")
                self.assertNotIn("private_key", on_disk)

                info = await handlers.get_wallet_info()
                self.assertTrue(info.get("active"))
                self.assertNotIn("private_key", info)
                self.assertNotIn("seed_phrase", info)

                loaded = await handlers.load_wallet(private_key)
                self.assertEqual(loaded.get("name"), "simple")
                active_again = await handlers.get_wallet_info()
                self.assertTrue(active_again.get("active"))
                self.assertNotIn("private_key", active_again)
                self.assertNotIn("seed_phrase", active_again)

                node.config.set("wallet_debug_secrets", True)
                debug_info = await handlers.get_wallet_info()
                self.assertEqual(debug_info.get("private_key"), private_key)
                self.assertTrue(bool(debug_info.get("seed_phrase")))

                node.config.set("network", "mainnet")
                node.config.set("debug", False)
                blocked_info = await handlers.get_wallet_info()
                self.assertNotIn("private_key", blocked_info)
                self.assertNotIn("seed_phrase", blocked_info)

                node.config.set("debug", True)
                dev_info = await handlers.get_wallet_info()
                self.assertEqual(dev_info.get("private_key"), private_key)
                self.assertTrue(bool(dev_info.get("seed_phrase")))

        asyncio.run(run())

    def test_getnewaddress_derives_next_child_for_active_wallet(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)

                created = await handlers.create_wallet()
                first_addr = created["address"]
                second_addr = await handlers.get_new_address()
                third_addr = await handlers.get_new_address()
                self.assertNotEqual(first_addr, second_addr)
                self.assertNotEqual(second_addr, third_addr)

                manager = node.simple_wallet_manager
                self.assertIsNotNone(manager)
                assert manager is not None
                # Single deterministic wallet file should be updated in place.
                self.assertEqual(len(manager.list_wallets()), 1)

                info = await handlers.get_wallet_info()
                self.assertEqual(info.get("address"), third_addr)

        asyncio.run(run())

    def test_imported_wallet_getnewaddress_keeps_compatibility(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)
                imported = await handlers.load_wallet("1")
                self.assertEqual(imported.get("name"), "simple")

                first_info = await handlers.get_wallet_info()
                addr_before = first_info.get("address")
                addr_after = await handlers.get_new_address()
                # Imported wallet path is compatibility mode; address may stay unchanged.
                self.assertTrue(bool(addr_after))
                self.assertEqual(addr_before, addr_after)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
