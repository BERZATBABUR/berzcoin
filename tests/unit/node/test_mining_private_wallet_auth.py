"""Regression tests for mining authorization via active private-key wallet identity."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.mining_control import MiningControlHandlers
from node.wallet.simple_wallet import SimpleWalletManager


class _Config:
    def __init__(self):
        self._values = {
            "network": "regtest",
            "miningaddress": "",
            "mining": False,
            "mining_threads": 1,
        }

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        self._values[key] = value


class _Miner:
    def __init__(self):
        self.is_mining = False
        self.blocks_mined = 0
        self.total_hashes = 0
        self.mining_address = ""

    async def start_mining(self, mining_address=None, threads=1):
        _ = threads
        if mining_address:
            self.mining_address = mining_address
        self.is_mining = True

    async def stop_mining(self):
        self.is_mining = False

    def get_stats(self):
        return {
            "mining": self.is_mining,
            "blocks_mined": self.blocks_mined,
            "total_hashes": self.total_hashes,
            "avg_hashrate": 0.0,
            "uptime": 0.0,
            "mining_address": self.mining_address,
        }


class _ChainState:
    class _Params:
        @staticmethod
        def get_network_name():
            return "regtest"

    params = _Params()

    @staticmethod
    def get_best_height():
        return 0

    @staticmethod
    def get_header(_height):
        class _Header:
            bits = 0x1D00FFFF

        return _Header()


class _Node:
    def __init__(self, manager):
        self.config = _Config()
        self.miner = _Miner()
        self.simple_wallet_manager = manager
        self.chainstate = _ChainState()


class TestMiningPrivateWalletAuth(unittest.TestCase):
    def test_allows_mining_address_independent_from_active_wallet(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                manager = SimpleWalletManager(Path(tmp))
                node = _Node(manager)
                handlers = MiningControlHandlers(node)

                missing = await handlers.set_generate(True, threads=1)
                self.assertIn("Set mining address", missing.get("error", ""))

                wallet_a = manager.create_wallet()
                manager.activate_wallet(wallet_a.private_key_hex)

                bad = await handlers.set_mining_address("not-a-valid-address")
                self.assertIn("Invalid mining address", bad.get("error", ""))

                ok_addr = await handlers.set_mining_address(wallet_a.address)
                self.assertEqual(ok_addr.get("new_address"), wallet_a.address)

                wallet_b = manager.create_wallet()
                manager.activate_wallet(wallet_b.private_key_hex)

                started = await handlers.set_generate(True, threads=1)
                self.assertEqual(started.get("status"), "started")
                self.assertTrue(node.miner.is_mining)
                self.assertEqual(node.miner.mining_address, wallet_a.address)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
