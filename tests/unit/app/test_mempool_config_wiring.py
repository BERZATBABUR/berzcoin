"""Unit tests for operator mempool config wiring into runtime policy/limits."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.app.main import BerzCoinNode
from node.app.modes import ModeManager


class _ChainstateStub:
    def __init__(self):
        self.params = type("Params", (), {"custom_activation_heights": {}, "coinbase_maturity": 100})()

    def get_best_height(self) -> int:
        return 0

    def get_best_block_hash(self) -> str:
        return "00" * 32

    def get_utxo(self, _txid: str, _index: int):
        return None

    def transaction_exists(self, _txid: str) -> bool:
        return False


class TestMempoolConfigWiring(unittest.TestCase):
    def test_init_mempool_uses_operator_config_thresholds(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = BerzCoinNode()
                node.config.set("datadir", str(Path(tmp)))
                node.config.set("network", "regtest")
                node.mode_manager = ModeManager(node.config)
                node.chainstate = _ChainstateStub()
                node.config.set("persistmempool", False)
                node.config.set("mempool_min_relay_fee", 9)
                node.config.set("mempool_rolling_floor_halflife_secs", 321)
                node.config.set("mempool_max_transactions", 4321)
                node.config.set("mempool_max_ancestors", 17)
                node.config.set("mempool_max_package_count", 19)

                ok = await node._init_mempool()
                self.assertTrue(ok)
                self.assertIsNotNone(node.mempool)
                self.assertEqual(int(node.mempool.policy.min_relay_fee), 9)
                self.assertEqual(int(node.mempool.limits.max_transactions), 4321)
                self.assertEqual(int(node.mempool.limits.max_ancestors), 17)
                self.assertEqual(int(node.mempool.limits.max_package_count), 19)
                self.assertEqual(int(node.mempool._min_fee_floor_half_life_secs), 321)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

