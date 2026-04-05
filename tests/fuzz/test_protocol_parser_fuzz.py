"""Fuzz tests for protocol parser hardening."""

import os
import random
import struct
import unittest

from shared.protocol.codec import MessageCodec, MessageHeader
from shared.protocol.messages import InvMessage


class TestProtocolParserFuzz(unittest.TestCase):
    def setUp(self) -> None:
        self.seed = int(os.getenv("BERZ_FUZZ_SEED", "20260405"))
        self.samples = int(os.getenv("BERZ_FUZZ_SAMPLES", "600"))
        self.rng = random.Random(self.seed)

    def _random_blob(self, max_len: int = 256) -> bytes:
        length = self.rng.randint(0, max_len)
        return bytes(self.rng.getrandbits(8) for _ in range(length))

    def test_message_header_fuzz_does_not_crash(self) -> None:
        """Malformed headers should fail closed, not crash the process."""
        accepted_errors = (ValueError, struct.error, UnicodeDecodeError)

        for _ in range(self.samples):
            blob = self._random_blob(320)
            try:
                header, consumed = MessageHeader.deserialize(blob, network="regtest")
                self.assertIsInstance(header.command, bytes)
                self.assertLessEqual(consumed, len(blob))
            except accepted_errors:
                pass

    def test_codec_decode_fuzz_does_not_crash(self) -> None:
        accepted_errors = (ValueError, struct.error, UnicodeDecodeError)
        codec = MessageCodec(network="regtest")

        for _ in range(self.samples):
            blob = self._random_blob(320)
            try:
                command, payload, consumed = codec.decode(blob)
                self.assertIsInstance(command, bytes)
                self.assertIsInstance(payload, bytes)
                self.assertLessEqual(consumed, len(blob))
            except accepted_errors:
                pass

    def test_oversized_payload_header_rejected(self) -> None:
        payload = b"\x01\x02\x03"
        header = (
            MessageHeader.MAGIC_BYTES["regtest"]
            + b"inv\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + struct.pack("<I", 50_000_000)
            + b"\x00\x00\x00\x00"
            + payload
        )

        with self.assertRaises(ValueError):
            MessageHeader.deserialize(header, network="regtest")

    def test_invalid_header_magic_rejected(self) -> None:
        payload = b"abc"
        bad_magic = b"\x00\x11\x22\x33"
        good = MessageHeader("ping", payload, network="regtest").serialize()
        raw = bad_magic + good[4:]

        with self.assertRaises(ValueError):
            MessageHeader.deserialize(raw, network="regtest")

    def test_invalid_inventory_payloads_rejected_or_bounded(self) -> None:
        accepted_errors = (ValueError, struct.error, UnicodeDecodeError)

        # Explicitly truncated inventory payload.
        truncated = b"\x03" + b"\x01\x00\x00\x00" + b"\x11" * 10
        with self.assertRaises((ValueError, struct.error)):
            InvMessage.deserialize(truncated)

        for _ in range(self.samples):
            blob = self._random_blob(400)
            try:
                inv, offset = InvMessage.deserialize(blob)
                self.assertLessEqual(offset, len(blob))
                self.assertIsInstance(inv.inventory, list)
            except accepted_errors:
                pass


if __name__ == "__main__":
    unittest.main()
