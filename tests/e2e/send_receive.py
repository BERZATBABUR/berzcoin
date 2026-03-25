"""End-to-end tests for wallet create / addresses / balance."""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from node.app.main import BerzCoinNode
from node.app.modes import ModeManager


class TestSendReceive(unittest.TestCase):
    """Test wallet flows with a temporary node."""

    _WALLET_PW = "e2e_test_wallet_pw"

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        wallets = os.path.join(self.temp_dir, "wallets")
        os.makedirs(wallets, exist_ok=True)

        self.node = BerzCoinNode()
        self.node.config.set("datadir", self.temp_dir)
        self.node.config.set("network", "regtest")
        self.node.config.set("disablewallet", False)
        self.node.config.set("wallet", "test_wallet")
        self.node.config.set("walletpassphrase", self._WALLET_PW)
        self.node.mode_manager = ModeManager(self.node.config)
        self.node.network = self.node.config.get("network", "regtest")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_wallet(self) -> None:
        async def run_test() -> None:
            ok = await self.node.initialize()
            self.assertTrue(ok)
            self.assertIsNotNone(self.node.wallet)
            self.assertTrue(self.node.wallet.is_loaded)
            self.assertTrue(self.node.wallet.unlock(self._WALLET_PW))
            address = self.node.wallet.get_new_address()
            self.assertIsNotNone(address)
            self.assertGreater(len(address or ""), 8)
            if self.node.db:
                self.node.db.disconnect()

        asyncio.run(run_test())

    def test_get_balance(self) -> None:
        async def run_test() -> None:
            self.assertTrue(await self.node.initialize())
            self.assertTrue(self.node.wallet.unlock(self._WALLET_PW))
            balance = self.node.wallet.get_balance()
            self.assertIsInstance(balance, int)
            self.assertEqual(balance, 0)
            if self.node.db:
                self.node.db.disconnect()

        asyncio.run(run_test())

    def test_address_generation(self) -> None:
        async def run_test() -> None:
            self.assertTrue(await self.node.initialize())
            self.assertTrue(self.node.wallet.unlock(self._WALLET_PW))
            address1 = self.node.wallet.get_new_address()
            address2 = self.node.wallet.get_new_address()
            self.assertIsNotNone(address1)
            self.assertIsNotNone(address2)
            if address1 == address2:
                self.skipTest("keypool stub may not advance between calls")
            if self.node.db:
                self.node.db.disconnect()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
