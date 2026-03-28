"""Unit tests for block sync batching behavior."""

import asyncio
import unittest

from node.p2p.sync import BlockSync
from shared.protocol.messages import GetDataMessage, InvMessage


class _Hdr:
    def __init__(self, b: bytes):
        self._b = b

    def hash(self) -> bytes:
        return self._b


class _HeaderChain:
    def __init__(self, headers):
        self._headers = headers

    def get_header(self, height: int):
        return self._headers.get(height)


class _ChainStateStub:
    def __init__(self, best_height: int, headers):
        self._best = best_height
        self.header_chain = _HeaderChain(headers)

    def get_best_height(self) -> int:
        return self._best


class _PeerStub:
    def __init__(self, peer_height: int):
        self.peer_height = peer_height
        self.sent = []

    async def send_message(self, command: str, payload: bytes) -> None:
        self.sent.append((command, payload))


class TestBlockSyncBatching(unittest.TestCase):
    def test_constructor_applies_batch_and_timeout_overrides(self) -> None:
        chain = _ChainStateStub(best_height=0, headers={})
        sync = BlockSync(chain, getdata_batch_size=32, block_request_timeout_secs=9)
        self.assertEqual(sync.getdata_batch_size, 32)
        self.assertEqual(sync._block_request_timeout_secs, 9)

    def test_getdata_requests_are_batched(self) -> None:
        async def run() -> None:
            headers = {
                h: _Hdr(h.to_bytes(32, "little"))
                for h in range(11, 211)
            }
            chain = _ChainStateStub(best_height=10, headers=headers)
            peer = _PeerStub(peer_height=210)
            sync = BlockSync(chain)
            sync.getdata_batch_size = 64

            await sync._request_blocks(peer)

            self.assertGreater(len(peer.sent), 1)
            total_items = 0
            for command, payload in peer.sent:
                self.assertEqual(command, "getdata")
                msg, _ = GetDataMessage.deserialize(payload)
                self.assertLessEqual(len(msg.inventory), 64)
                total_items += len(msg.inventory)
                for inv_type, _ in msg.inventory:
                    self.assertEqual(inv_type, InvMessage.InvType.MSG_BLOCK)

            self.assertEqual(total_items, 200)
            self.assertEqual(sync.blocks_requested, 200)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
