"""Regression tests for explicit activatewallet RPC method."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.wallet_control import WalletControlHandlers
from node.wallet.simple_wallet import SimpleWalletManager


class _Config:
    def __init__(self, datadir: Path):
        self._datadir = datadir

    def get_datadir(self) -> Path:
        return self._datadir


class _Node:
    def __init__(self, datadir: Path):
        self.config = _Config(datadir)
        self.simple_wallet_manager = None


class TestActivateWalletRPC(unittest.TestCase):
    def test_activatewallet_activates_by_private_key(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                manager = SimpleWalletManager(Path(tmp))
                wallet = manager.create_wallet()
                node.simple_wallet_manager = manager

                handlers = WalletControlHandlers(node)
                manager.active_wallet = None
                manager.active_private_key = None

                result = await handlers.activate_wallet(wallet.private_key_hex)
                self.assertEqual(result.get("status"), "activated")
                self.assertEqual(result.get("address"), wallet.address)
                self.assertEqual(result.get("public_key"), wallet.public_key_hex)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
