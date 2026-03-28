"""Regression tests for private-key wallet activation flow."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.wallet import WalletHandlers


class _Config:
    def __init__(self, datadir: Path):
        self._datadir = datadir

    def get_datadir(self) -> Path:
        return self._datadir

    def get(self, key, default=None):
        if key == "network":
            return "regtest"
        return default


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

                info = await handlers.get_wallet_info()
                self.assertTrue(info.get("active"))
                self.assertEqual(info.get("private_key"), private_key)

                loaded = await handlers.load_wallet(private_key)
                self.assertEqual(loaded.get("name"), "simple")
                active_again = await handlers.get_wallet_info()
                self.assertTrue(active_again.get("active"))
                self.assertEqual(active_again.get("private_key"), private_key)
                self.assertTrue(bool(active_again.get("seed_phrase")))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
