"""Integration tests for node components."""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from node.app.config import Config
from node.app.main import BerzCoinNode
from node.app.modes import ModeManager


class TestNode(unittest.TestCase):
    """Test node integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.config = Config()
        self.config.set("datadir", self.temp_dir)
        self.config.set("network", "regtest")
        self.config.set("port", 18444)
        self.config.set("rpcport", 18443)
        self.config.set("rpcbind", "127.0.0.1")
        self.config.set("walletpassphrase", "integration_test_wallet_pw")
        self.config.set("debug", True)

        self.node = BerzCoinNode()
        self.node.config = self.config
        self.node.mode_manager = ModeManager(self.config)
        self.node.network = self.config.get("network", "mainnet")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_node_initialization(self) -> None:
        async def run_test() -> None:
            result = await self.node.initialize()
            self.assertTrue(result)
            self.assertIsNotNone(self.node.db)
            self.assertIsNotNone(self.node.chainstate)
            if self.node.db:
                self.node.db.disconnect()

        asyncio.run(run_test())

    def test_config_loading(self) -> None:
        self.assertEqual(self.config.get("network"), "regtest")
        self.assertEqual(self.config.get("datadir"), self.temp_dir)
        self.assertTrue(self.config.validate())

    def test_datadir_creation(self) -> None:
        self.assertTrue(self.config.validate())
        datadir = self.config.get_datadir()
        self.assertTrue(datadir.exists())
        self.assertTrue(datadir.is_dir())


if __name__ == "__main__":
    unittest.main()
