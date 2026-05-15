"""Strict serialization/deserialization roundtrip and malformed-data tests."""

from __future__ import annotations

import unittest

from shared.core.block import (
    Block,
    BlockHeader,
    deserialize_block_strict,
    deserialize_header_strict,
)
from shared.core.hashes import hash256
from shared.core.merkle import merkle_root
from shared.core.transaction import (
    Transaction,
    TxIn,
    TxOut,
    deserialize_transaction_strict,
)
from shared.protocol.codec import MessageCodec


def _tx(
    prev: bytes = b"\x11" * 32,
    idx: int = 0,
    script_sig: bytes = b"\x01\x02",
    seq: int = 0xFFFFFFFE,
    outs: list[TxOut] | None = None,
    version: int = 2,
) -> Transaction:
    t = Transaction(version=version)
    t.vin = [TxIn(prev_tx_hash=prev, prev_tx_index=idx, script_sig=script_sig, sequence=seq)]
    t.vout = outs or [TxOut(10_000, b"\x51")]
    return t


class TestRoundtripStrict(unittest.TestCase):
    def test_transaction_roundtrip_normal(self) -> None:
        tx = _tx()
        raw = tx.serialize(include_witness=False)
        dec = deserialize_transaction_strict(raw)
        self.assertEqual(dec.serialize(include_witness=False), raw)

    def test_transaction_roundtrip_coinbase(self) -> None:
        tx = Transaction(version=1)
        tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x02\x01", sequence=0xFFFFFFFF)]
        tx.vout = [TxOut(50_000, b"\x51")]
        raw = tx.serialize(include_witness=False)
        dec = deserialize_transaction_strict(raw)
        self.assertEqual(dec.serialize(include_witness=False), raw)

    def test_transaction_roundtrip_multi_input_output_and_large_scripts(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=b"\x21" * 32, prev_tx_index=0, script_sig=b"\x6a" * 120, sequence=1),
            TxIn(prev_tx_hash=b"\x22" * 32, prev_tx_index=1, script_sig=b"\x51" * 160, sequence=2),
        ]
        tx.vout = [
            TxOut(12_345, b"\x76\xa9\x14" + b"\x01" * 20 + b"\x88\xac"),
            TxOut(54_321, b"\xa9\x14" + b"\x02" * 20 + b"\x87"),
            TxOut(1_000, b"\x00\x14" + b"\x03" * 20),
        ]
        raw = tx.serialize(include_witness=False)
        dec = deserialize_transaction_strict(raw)
        self.assertEqual(dec.serialize(include_witness=False), raw)

    def test_block_header_roundtrip(self) -> None:
        h = BlockHeader(
            version=3,
            prev_block_hash=b"\xaa" * 32,
            merkle_root=b"\xbb" * 32,
            timestamp=1_700_000_000,
            bits=0x207FFFFF,
            nonce=123,
        )
        raw = h.serialize()
        dec = deserialize_header_strict(raw)
        self.assertEqual(dec.serialize(), raw)

    def test_block_roundtrip_one_and_multiple_transactions(self) -> None:
        tx1 = _tx()
        mr1 = merkle_root([tx1.txid()]) or (b"\x00" * 32)
        b1 = Block(BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=mr1, timestamp=1_700_000_001), [tx1])
        raw1 = b1.serialize(include_witness=False)
        self.assertEqual(deserialize_block_strict(raw1).serialize(include_witness=False), raw1)

        tx2 = _tx(prev=b"\x33" * 32, idx=1, script_sig=b"\x04\x05")
        mr2 = merkle_root([tx1.txid(), tx2.txid()]) or (b"\x00" * 32)
        b2 = Block(BlockHeader(prev_block_hash=b"\x01" * 32, merkle_root=mr2, timestamp=1_700_000_002), [tx1, tx2])
        raw2 = b2.serialize(include_witness=False)
        self.assertEqual(deserialize_block_strict(raw2).serialize(include_witness=False), raw2)

    def test_malformed_truncated_transaction_paths(self) -> None:
        tx = _tx(script_sig=b"\x99" * 10)
        raw = tx.serialize(include_witness=False)
        with self.assertRaises(Exception):
            deserialize_transaction_strict(raw[:-1])  # truncated locktime
        with self.assertRaises(Exception):
            deserialize_transaction_strict(raw[:36])  # truncated input

    def test_malformed_truncated_script_bytes(self) -> None:
        # version + vin=1 + prevhash + idx + scriptlen=5 but only 2 bytes + seq + vout=0 + locktime
        raw = (
            (2).to_bytes(4, "little")
            + b"\x01"
            + (b"\x11" * 32)
            + (0).to_bytes(4, "little")
            + b"\x05"
            + b"\xaa\xbb"
        )
        with self.assertRaises(Exception):
            deserialize_transaction_strict(raw)

    def test_malformed_truncated_block_paths(self) -> None:
        tx = _tx()
        mr = merkle_root([tx.txid()]) or (b"\x00" * 32)
        block = Block(BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=mr, timestamp=1_700_000_010), [tx])
        raw = block.serialize(include_witness=False)
        with self.assertRaises(Exception):
            deserialize_header_strict(raw[:60])  # truncated header
        with self.assertRaises(Exception):
            deserialize_block_strict(raw[:-3])  # truncated tx list / tail

    def test_invalid_varint_rejected(self) -> None:
        # varint marker 0xfd requires two more bytes; absent here.
        raw = (1).to_bytes(4, "little") + b"\xfd"
        with self.assertRaises(Exception):
            deserialize_transaction_strict(raw)

    def test_extra_bytes_rejected(self) -> None:
        tx = _tx()
        with self.assertRaises(ValueError):
            deserialize_transaction_strict(tx.serialize(include_witness=False) + b"\x00\x00")

        mr = merkle_root([tx.txid()]) or (b"\x00" * 32)
        block = Block(BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=mr, timestamp=1_700_000_010), [tx])
        with self.assertRaises(ValueError):
            deserialize_block_strict(block.serialize(include_witness=False) + b"\x01")

    def test_hash_order_conventions_and_no_double_reversal(self) -> None:
        tx = _tx()
        internal_txid = tx.txid().hex()
        display_txid = tx.txid_hex(display_order=True)
        self.assertEqual(display_txid, tx.txid()[::-1].hex())
        self.assertEqual(internal_txid, tx.txid_hex(display_order=False))
        self.assertEqual(bytes.fromhex(display_txid)[::-1].hex(), internal_txid)

        h = BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=b"\x44" * 32, timestamp=1_700_000_030, nonce=3)
        internal_bh = h.hash().hex()
        display_bh = h.hash_hex(display_order=True)
        self.assertEqual(display_bh, h.hash()[::-1].hex())
        self.assertEqual(internal_bh, h.hash_hex(display_order=False))
        self.assertEqual(bytes.fromhex(display_bh)[::-1].hex(), internal_bh)

    def test_bytes_safe_for_hash_disk_p2p_rpc_hex(self) -> None:
        tx = _tx()
        raw = tx.serialize(include_witness=True)
        # Hashing determinism
        self.assertEqual(hash256(raw), hash256(bytes(raw)))
        # RPC hex encoding roundtrip
        self.assertEqual(bytes.fromhex(raw.hex()), raw)
        # P2P envelope roundtrip
        codec = MessageCodec(network="regtest")
        wire = codec.encode("tx", raw)
        cmd, payload, consumed = codec.decode(wire)
        self.assertEqual(bytes(cmd).rstrip(b"\x00"), b"tx")
        self.assertEqual(payload, raw)
        self.assertEqual(consumed, len(wire))


if __name__ == "__main__":
    unittest.main()
