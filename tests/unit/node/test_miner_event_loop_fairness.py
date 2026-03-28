"""Unit tests ensuring miner work remains cooperative with the event loop."""

import asyncio
import unittest

from node.mining.miner import MiningNode


class _Params:
    pow_target_spacing = 120
    max_block_weight = 4_000_000
    pow_limit = (1 << 256) - 1

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


class _Header:
    bits = 0x207FFFFF  # regtest-style easy target

    def __init__(self):
        self.nonce = 0

    @staticmethod
    def is_valid_pow(_target: int) -> bool:
        return True


class TestMinerEventLoopFairness(unittest.TestCase):
    def test_mine_block_yields_before_return(self) -> None:
        async def run() -> None:
            miner = MiningNode(_ChainState(), _Mempool(), "bcrt1qexample")
            miner.is_mining = True

            callback_state = {"ran": False}
            loop = asyncio.get_running_loop()
            loop.call_soon(callback_state.__setitem__, "ran", True)

            result = await miner._mine_block(_Header(), max_nonce=1)

            self.assertIsNotNone(result)
            self.assertTrue(
                callback_state["ran"],
                "Expected miner._mine_block to yield to event loop before returning",
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
