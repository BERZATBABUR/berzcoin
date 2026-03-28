"""Integration regressions for block relay and deterministic rejoin replay behavior."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from node.app.bootstrap import NodeBootstrap
from node.p2p.connman import ConnectionManager
from shared.core.block import Block, BlockHeader


class _Peer:
    def __init__(self, address: str):
        self.address = address
        self.connected = True
        self.sent = []

    async def send_message(self, command, payload):
        self.sent.append((command, payload))


class _AddrMan:
    pass


class _ChainState:
    def __init__(self, best_height: int):
        self._best_height = best_height
        self.blocks_store = object()

    def get_best_height(self):
        return self._best_height


class _P2P:
    @staticmethod
    def get_best_height_peer():
        return None


class _UTXO:
    pass


class TestRelayAndRejoinPaths(unittest.TestCase):
    def test_connman_broadcast_block_sends_inv_to_connected_peers(self) -> None:
        async def run() -> None:
            connman = ConnectionManager(addrman=_AddrMan(), dns_seeds=[])
            p1 = _Peer("127.0.0.1:10001")
            p2 = _Peer("127.0.0.1:10002")
            connman.peers[p1.address] = p1
            connman.peers[p2.address] = p2

            header = BlockHeader(
                version=1,
                prev_block_hash=b"\x00" * 32,
                merkle_root=b"\x11" * 32,
                timestamp=1,
                bits=0x207fffff,
                nonce=0,
            )
            block = Block(header, [])

            await connman.broadcast_block(block)
            self.assertEqual(len(p1.sent), 1)
            self.assertEqual(len(p2.sent), 1)
            self.assertEqual(p1.sent[0][0], "inv")
            self.assertEqual(p2.sent[0][0], "inv")

        asyncio.run(run())

    def test_bootstrap_sync_full_chain_defaults_to_replay_rebuild(self) -> None:
        async def run() -> None:
            bootstrap = NodeBootstrap(_ChainState(7), _P2P(), _UTXO())
            bootstrap.replay_chain_and_rebuild_utxo = AsyncMock(return_value=True)

            ok = await bootstrap.sync_full_chain()
            self.assertTrue(ok)
            bootstrap.replay_chain_and_rebuild_utxo.assert_awaited_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
