"""Unit tests for compact block reconstruction and fallback behavior."""

import asyncio
import unittest
from types import MethodType, SimpleNamespace

from node.app.main import BerzCoinNode
from node.p2p.peer import Peer
from node.p2p.sync import BlockSync
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.protocol.messages import CmpctBlockMessage
from shared.protocol.messages import BlockTxnMessage


class _FakePeer:
    def __init__(self, address: str = "198.51.100.9:8333"):
        self.address = address
        self.getblocktxn_calls = []
        self.getdata_calls = []
        self.compact_results = []

    async def send_getblocktxn(self, block_hash: bytes, indexes):
        self.getblocktxn_calls.append((bytes(block_hash), list(indexes)))

    async def send_getdata(self, inv_type: int, inv_hash: bytes):
        self.getdata_calls.append((int(inv_type), bytes(inv_hash)))

    def record_compact_result(self, success: bool) -> None:
        self.compact_results.append(bool(success))


class _FakeBlockSync:
    def __init__(self):
        self.registered = []
        self.resolved = []

    def register_compact_request(self, block_hash_hex: str, peer_addr: str, mode: str) -> None:
        self.registered.append((str(block_hash_hex), str(peer_addr), str(mode)))

    def resolve_compact_request(self, block_hash_hex: str) -> None:
        self.resolved.append(str(block_hash_hex))


def _coinbase_spend_block() -> Block:
    coinbase = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=b"\x00" * 32,
                prev_tx_index=0xFFFFFFFF,
                script_sig=b"\x01",
                sequence=0xFFFFFFFF,
            )
        ],
        outputs=[TxOut(value=1000, script_pubkey=b"\x51")],
        locktime=0,
    )
    spend = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=coinbase.txid(),
                prev_tx_index=0,
                script_sig=b"\x51",
                sequence=0xFFFFFFFF,
            )
        ],
        outputs=[TxOut(value=900, script_pubkey=b"\x51")],
        locktime=0,
    )
    header = BlockHeader(merkle_root=coinbase.txid())
    return Block(header=header, transactions=[coinbase, spend])


class TestCompactBlockFlow(unittest.TestCase):
    def test_peer_auto_downgrades_after_compact_failures(self) -> None:
        peer = Peer("127.0.0.1", 8333)
        peer.prefers_compact_blocks = True
        peer.record_compact_result(False)
        peer.record_compact_result(False)
        self.assertTrue(peer.prefers_compact_blocks)
        peer.record_compact_result(False)
        self.assertFalse(peer.prefers_compact_blocks)

    def test_blocksync_compact_request_timeout_tracking(self) -> None:
        sync = BlockSync(chainstate=SimpleNamespace(get_best_height=lambda: 0))
        sync.register_compact_request("aa" * 32, "p", "getblocktxn")
        self.assertEqual(len(sync.get_stale_compact_requests()), 0)
        # Force stale by rewinding create time.
        sync._pending_compact_requests["aa" * 32]["created_at"] = 0.0
        stale = sync.get_stale_compact_requests()
        self.assertEqual(stale, ["aa" * 32])

    def test_cmpctblock_requests_getblocktxn_when_missing_small(self) -> None:
        async def run():
            node = BerzCoinNode()
            node._known_blocks = set()
            node._pending_compact_blocks = {}
            node._compact_max_missing_indexes = 8
            node.block_sync = _FakeBlockSync()
            node.chainstate = SimpleNamespace(
                block_index=SimpleNamespace(get_block=lambda _h: None)
            )
            node.mempool = None
            node._peer_msg_window = {}
            node._max_msgs_per_sec = 999999
            node.connman = None

            async def _noop_ensure(self):
                return None

            node._ensure_block_sync = MethodType(_noop_ensure, node)
            peer = _FakePeer()
            block = _coinbase_spend_block()
            msg = CmpctBlockMessage.from_block(block, nonce=77)
            await node._on_p2p_message(peer, "cmpctblock", msg.serialize())

            self.assertEqual(len(peer.getblocktxn_calls), 1)
            self.assertEqual(len(peer.getdata_calls), 0)
            self.assertEqual(len(node.block_sync.registered), 1)
            self.assertEqual(node.block_sync.registered[0][2], "getblocktxn")

        asyncio.run(run())

    def test_cmpctblock_falls_back_to_getdata_when_missing_large(self) -> None:
        async def run():
            node = BerzCoinNode()
            node._known_blocks = set()
            node._pending_compact_blocks = {}
            node._compact_max_missing_indexes = 0
            node.block_sync = _FakeBlockSync()
            node.chainstate = SimpleNamespace(
                block_index=SimpleNamespace(get_block=lambda _h: None)
            )
            node.mempool = None
            node._peer_msg_window = {}
            node._max_msgs_per_sec = 999999
            node.connman = None

            async def _noop_ensure(self):
                return None

            node._ensure_block_sync = MethodType(_noop_ensure, node)
            peer = _FakePeer()
            block = _coinbase_spend_block()
            msg = CmpctBlockMessage.from_block(block, nonce=88)
            await node._on_p2p_message(peer, "cmpctblock", msg.serialize())

            self.assertEqual(len(peer.getblocktxn_calls), 0)
            self.assertEqual(len(peer.getdata_calls), 1)
            self.assertEqual(len(node.block_sync.registered), 1)
            self.assertEqual(node.block_sync.registered[0][2], "getdata")

        asyncio.run(run())

    def test_blocktxn_mismatch_falls_back_to_getdata(self) -> None:
        async def run():
            node = BerzCoinNode()
            node._known_blocks = set()
            node._pending_compact_blocks = {}
            node._compact_max_missing_indexes = 8
            node.block_sync = _FakeBlockSync()
            node.chainstate = SimpleNamespace(
                block_index=SimpleNamespace(get_block=lambda _h: None)
            )
            node.mempool = None
            node._peer_msg_window = {}
            node._max_msgs_per_sec = 999999
            node.connman = None

            peer = _FakePeer()
            block = _coinbase_spend_block()
            msg = CmpctBlockMessage.from_block(block, nonce=99)
            block_hash = msg.block_hash()
            block_hash_hex = block_hash[::-1].hex()

            node._pending_compact_blocks[block_hash_hex] = {
                "peer_addr": peer.address,
                "block_hash": block_hash,
                "header": msg.header,
                "nonce": msg.nonce,
                "tx_slots": {0: block.transactions[0].serialize()},
                "total_txs": 2,
                "missing_indexes": [1],
                "requested_indexes": [1],
                "created_at": 0.0,
            }
            # Mismatch: requested one index but return zero tx payloads.
            btx = BlockTxnMessage(block_hash=block_hash, transactions=[])
            await node._on_p2p_message(peer, "blocktxn", btx.serialize())

            self.assertEqual(len(peer.getdata_calls), 1)
            self.assertEqual(node.block_sync.registered[0][2], "getdata")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
