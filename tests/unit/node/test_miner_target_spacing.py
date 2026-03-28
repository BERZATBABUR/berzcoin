"""Unit tests for miner target params and coinbase uniqueness."""

import asyncio
import time
import unittest

from node.mining.miner import MiningNode
from node.wallet.simple_wallet import SimpleWallet


class _Params:
    pow_target_spacing = 120
    max_block_weight = 4_000_000

    @staticmethod
    def retarget_interval_blocks():
        return 2016


class _ChainState:
    def __init__(self):
        self.params = _Params()
        self.network = "regtest"


class _Mempool:
    async def get_transactions(self):
        return []


class TestMinerTargetSpacing(unittest.TestCase):
    def test_target_time_comes_from_consensus_params(self) -> None:
        miner = MiningNode(_ChainState(), _Mempool(), "bcrt1qexample")
        self.assertEqual(miner.target_time, 120)

    def test_coinbase_extra_nonce_changes_txid(self) -> None:
        wallet = SimpleWallet.create()
        miner = MiningNode(_ChainState(), _Mempool(), wallet.address)
        cb0 = miner._create_coinbase(height=1, value=50_0000_0000, extra_nonce=0)
        cb1 = miner._create_coinbase(height=1, value=50_0000_0000, extra_nonce=1)
        self.assertNotEqual(cb0.txid(), cb1.txid())

    def test_pacing_waits_for_target_spacing(self) -> None:
        async def run() -> None:
            miner = MiningNode(_ChainState(), _Mempool(), "bcrt1qexample")
            miner.target_time = 0.05
            t0 = time.perf_counter()
            await miner._pace_to_target_spacing(time.time())
            elapsed = time.perf_counter() - t0
            self.assertGreaterEqual(elapsed, 0.04)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
