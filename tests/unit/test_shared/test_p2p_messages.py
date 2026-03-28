"""Unit tests for compact-block P2P message envelopes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.core.block import Block, BlockHeader
from shared.core.hashes import hash256
from shared.protocol.messages import CmpctBlockMessage, SendCmpctMessage


class TestP2PMessages(unittest.TestCase):
    def test_sendcmpct_roundtrip(self) -> None:
        msg = SendCmpctMessage(announce=True, version=1)
        decoded, offset = SendCmpctMessage.deserialize(msg.serialize())
        self.assertEqual(offset, len(msg.serialize()))
        self.assertTrue(decoded.announce)
        self.assertEqual(decoded.version, 1)

    def test_cmpctblock_roundtrip(self) -> None:
        header = BlockHeader().serialize()
        msg = CmpctBlockMessage(
            header=header,
            nonce=42,
            shortids=[1, 0x112233445566],
            prefilled_txn=[(0, b"\x01\x02\x03")],
        )
        raw = msg.serialize()
        decoded, offset = CmpctBlockMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.header, header)
        self.assertEqual(decoded.nonce, 42)
        self.assertEqual(decoded.shortids, [1, 0x112233445566])
        self.assertEqual(decoded.prefilled_txn, [(0, b"\x01\x02\x03")])
        self.assertEqual(decoded.block_hash(), hash256(header))

    def test_cmpctblock_from_block(self) -> None:
        block = Block(header=BlockHeader(), transactions=[])
        msg = CmpctBlockMessage.from_block(block, nonce=99)
        self.assertEqual(msg.header, block.header.serialize())
        self.assertEqual(msg.nonce, 99)
        self.assertEqual(msg.shortids, [])
        self.assertEqual(msg.prefilled_txn, [])


if __name__ == "__main__":
    unittest.main()

