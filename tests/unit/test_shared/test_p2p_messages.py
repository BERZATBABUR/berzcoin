"""Unit tests for compact-block P2P message envelopes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.core.block import Block, BlockHeader
from shared.core.hashes import hash256
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.protocol.messages import (
    BlockTxnMessage,
    CmpctBlockMessage,
    GetBlockTxnMessage,
    JoinAttestMessage,
    JoinChallengeMessage,
    JoinRequestMessage,
    JoinResultMessage,
    SendCmpctMessage,
)


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
            outputs=[TxOut(value=1, script_pubkey=b"\x51")],
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
            outputs=[TxOut(value=1, script_pubkey=b"\x51")],
            locktime=0,
        )
        block = Block(header=BlockHeader(), transactions=[coinbase, spend])
        msg = CmpctBlockMessage.from_block(block, nonce=99)
        self.assertEqual(msg.header, block.header.serialize())
        self.assertEqual(msg.nonce, 99)
        self.assertEqual(len(msg.shortids), 1)
        self.assertEqual(len(msg.prefilled_txn), 1)
        self.assertEqual(msg.prefilled_txn[0][0], 0)

    def test_getblocktxn_roundtrip(self) -> None:
        msg = GetBlockTxnMessage(block_hash=b"\x11" * 32, indexes=[1, 4, 9])
        raw = msg.serialize()
        decoded, offset = GetBlockTxnMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.block_hash, b"\x11" * 32)
        self.assertEqual(decoded.indexes, [1, 4, 9])

    def test_blocktxn_roundtrip(self) -> None:
        msg = BlockTxnMessage(block_hash=b"\x22" * 32, transactions=[b"\x01\x02", b"\x03"])
        raw = msg.serialize()
        decoded, offset = BlockTxnMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.block_hash, b"\x22" * 32)
        self.assertEqual(decoded.transactions, [b"\x01\x02", b"\x03"])

    def test_join_request_roundtrip(self) -> None:
        msg = JoinRequestMessage(
            node_id="node-a",
            pubkey="02abcd",
            listen_port=8333,
            nonce=123,
            timestamp=456,
        )
        raw = msg.serialize()
        decoded, offset = JoinRequestMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.node_id, "node-a")
        self.assertEqual(decoded.pubkey, "02abcd")
        self.assertEqual(decoded.listen_port, 8333)
        self.assertEqual(decoded.nonce, 123)
        self.assertEqual(decoded.timestamp, 456)

    def test_join_challenge_roundtrip(self) -> None:
        msg = JoinChallengeMessage(challenge_id=9, challenge=b"\x01\x02\x03", expires_at=999)
        raw = msg.serialize()
        decoded, offset = JoinChallengeMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.challenge_id, 9)
        self.assertEqual(decoded.challenge, b"\x01\x02\x03")
        self.assertEqual(decoded.expires_at, 999)

    def test_join_attest_roundtrip(self) -> None:
        msg = JoinAttestMessage(
            candidate_node_id="candidate-x",
            verifier_node_id="verifier-y",
            verifier_pubkey="03deadbeef",
            challenge_id=11,
            signature=b"sig",
            timestamp=777,
        )
        raw = msg.serialize()
        decoded, offset = JoinAttestMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertEqual(decoded.candidate_node_id, "candidate-x")
        self.assertEqual(decoded.verifier_node_id, "verifier-y")
        self.assertEqual(decoded.verifier_pubkey, "03deadbeef")
        self.assertEqual(decoded.challenge_id, 11)
        self.assertEqual(decoded.signature, b"sig")
        self.assertEqual(decoded.timestamp, 777)

    def test_join_result_roundtrip(self) -> None:
        msg = JoinResultMessage(
            accepted=True,
            code=0,
            reason="accepted",
            required_votes=2,
            received_votes=2,
        )
        raw = msg.serialize()
        decoded, offset = JoinResultMessage.deserialize(raw)
        self.assertEqual(offset, len(raw))
        self.assertTrue(decoded.accepted)
        self.assertEqual(decoded.code, 0)
        self.assertEqual(decoded.reason, "accepted")
        self.assertEqual(decoded.required_votes, 2)
        self.assertEqual(decoded.received_votes, 2)


if __name__ == "__main__":
    unittest.main()
