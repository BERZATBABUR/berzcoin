"""Unit tests for relay behavior under adversarial INV traffic."""

import asyncio
import unittest

from node.p2p.peer_scoring import PeerScoringManager
from node.p2p.relay import TransactionRelay
from shared.protocol.messages import InvMessage


class _MempoolStub:
    def __init__(self):
        self.known = set()

    async def get_transaction(self, txid: str):
        return txid if txid in self.known else None

    async def add_transaction(self, tx, source_peer=None):
        return True


class _PeerStub:
    def __init__(self, address: str):
        self.address = address
        self.getdata = []

    async def send_getdata(self, inv_type: int, inv_hash: bytes) -> None:
        self.getdata.append((inv_type, inv_hash))


class TestRelayHardening(unittest.TestCase):
    def test_inv_spam_is_penalized_and_capped(self) -> None:
        async def run() -> None:
            scores = PeerScoringManager()
            relay = TransactionRelay(_MempoolStub(), peer_scores=scores)
            relay.max_inv_per_message = 3
            peer = _PeerStub("198.51.100.11:8333")
            inventory = [
                (InvMessage.InvType.MSG_TX, (i + 1).to_bytes(32, "little"))
                for i in range(10)
            ]
            inv = InvMessage(inventory=inventory)
            await relay.process_inv(peer, inv)

            self.assertEqual(len(peer.getdata), 3)
            self.assertLess(scores.get_score(peer.address).score, 0)

        asyncio.run(run())

    def test_pending_inv_is_trimmed(self) -> None:
        async def run() -> None:
            relay = TransactionRelay(_MempoolStub())
            relay.max_pending_inv = 2
            peer = _PeerStub("198.51.100.12:8333")
            inv = InvMessage(
                inventory=[
                    (InvMessage.InvType.MSG_TX, (1).to_bytes(32, "little")),
                    (InvMessage.InvType.MSG_TX, (2).to_bytes(32, "little")),
                    (InvMessage.InvType.MSG_TX, (3).to_bytes(32, "little")),
                ]
            )
            await relay.process_inv(peer, inv)
            self.assertEqual(relay.get_pending_count(), 2)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
