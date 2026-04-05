"""Unit tests for miner/template selection parity with mempool consensus filtering."""

import asyncio
import unittest

from node.mining.block_assembler import BlockAssembler
from node.mining.miner import MiningNode


class _ChainState:
    def __init__(self):
        self.params = type(
            "Params",
            (),
            {
                "max_block_weight": 4_000_000,
                "max_block_sigops": 20_000,
                "pow_target_spacing": 120,
            },
        )()
        self.network = "regtest"


class _Mempool:
    def __init__(self):
        self.called_max_weight = None
        self.transactions = {}

    async def get_transactions_for_block(self, max_weight: int):
        self.called_max_weight = int(max_weight)
        return ["tx1", "tx2"]


class TestMiningTemplateConsensusParity(unittest.TestCase):
    def test_miner_uses_consensus_filtered_mempool_selector(self) -> None:
        async def run() -> None:
            mempool = _Mempool()
            miner = MiningNode(_ChainState(), mempool, mining_address="bcrt1qexample")
            txs = await miner._select_transactions()
            self.assertEqual(txs, ["tx1", "tx2"])
            self.assertIsNotNone(mempool.called_max_weight)
            self.assertEqual(mempool.called_max_weight, 3_996_000)

        asyncio.run(run())

    def test_block_assembler_uses_consensus_filtered_mempool_selector(self) -> None:
        async def run() -> None:
            mempool = _Mempool()
            assembler = BlockAssembler(_ChainState(), mempool, coinbase_address="bcrt1qexample", network="regtest")
            txs = await assembler._select_transactions()
            self.assertEqual(txs, ["tx1", "tx2"])
            self.assertIsNotNone(mempool.called_max_weight)
            self.assertEqual(mempool.called_max_weight, 3_996_000)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
