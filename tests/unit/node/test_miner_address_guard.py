"""Unit tests for miner wallet-address guard auto-stop policy."""

import asyncio
import unittest

from node.mining.miner import MiningNode


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


class TestMinerAddressGuard(unittest.TestCase):
    def test_start_mining_denied_when_guard_fails(self) -> None:
        async def run() -> None:
            miner = MiningNode(
                _ChainState(),
                _Mempool(),
                mining_address="bcrt1qguarddeny",
                address_guard=lambda _addr: False,
            )
            await miner.start_mining()
            self.assertFalse(miner.is_mining)
            self.assertEqual(miner.last_stop_reason, "mining_address_wallet_mismatch")

        asyncio.run(run())

    def test_guard_allows_when_no_guard(self) -> None:
        miner = MiningNode(_ChainState(), _Mempool(), mining_address="bcrt1qok")
        self.assertTrue(miner._guard_allows_current_address())


if __name__ == "__main__":
    unittest.main()
