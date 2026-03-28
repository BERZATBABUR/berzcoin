"""Regression tests for bootstrap replay + UTXO rebuild mode."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from node.app.bootstrap import NodeBootstrap


class _ChainState:
    def __init__(self, best_height: int):
        self._best_height = best_height
        self.blocks_store = object()

    def get_best_height(self) -> int:
        return self._best_height


class _P2P:
    @staticmethod
    def get_best_height_peer():
        return None


class _UTXO:
    pass


class TestBootstrapReplayRebuild(unittest.TestCase):
    def test_replay_chain_rebuild_calls_reindexer(self) -> None:
        async def run() -> None:
            bootstrap = NodeBootstrap(_ChainState(12), _P2P(), _UTXO())
            with patch("node.app.bootstrap.Reindexer") as reindexer_cls:
                reindexer = reindexer_cls.return_value
                reindexer.run = AsyncMock(return_value=True)
                ok = await bootstrap.replay_chain_and_rebuild_utxo()

                self.assertTrue(ok)
                reindexer.run.assert_awaited_once_with(0, 12)

        asyncio.run(run())

    def test_sync_full_chain_no_peers_uses_replay_mode(self) -> None:
        async def run() -> None:
            bootstrap = NodeBootstrap(_ChainState(3), _P2P(), _UTXO())
            bootstrap.replay_chain_and_rebuild_utxo = AsyncMock(return_value=True)

            ok = await bootstrap.sync_full_chain(replay_rebuild=True)
            self.assertTrue(ok)
            bootstrap.replay_chain_and_rebuild_utxo.assert_awaited_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
