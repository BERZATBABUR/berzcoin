"""Reliability tests for peer framing/network checks and malformed message handling."""

from __future__ import annotations

import asyncio
import struct
import unittest
from unittest.mock import AsyncMock

from node.p2p.peer import Peer
from shared.protocol.codec import MessageCodec
from shared.protocol.messages import VersionMessage
from shared.protocol.versioning import VersionHandshake


class _Reader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readexactly(self, n: int) -> bytes:
        if not self._chunks:
            raise asyncio.IncompleteReadError(partial=b"", expected=n)
        chunk = self._chunks.pop(0)
        if len(chunk) != n:
            raise asyncio.IncompleteReadError(partial=chunk, expected=n)
        return chunk

    async def read(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks[0]
        if len(chunk) <= n:
            self._chunks.pop(0)
            return chunk
        out = chunk[:n]
        self._chunks[0] = chunk[n:]
        return out


class _Writer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class TestPeerProtocolReliability(unittest.TestCase):
    def test_handshake_network_matrix(self) -> None:
        cases = [("mainnet", True), ("testnet", True), ("regtest", True)]
        for network, expected_ok in cases:
            hs = VersionHandshake(expected_network=network)
            msg = VersionMessage(network=network, version=VersionHandshake.PROTOCOL_VERSION)
            ok, _err = hs.process_version(msg)
            self.assertEqual(ok, expected_ok)

        hs = VersionHandshake(expected_network="regtest")
        bad = VersionMessage(network="mainnet", version=VersionHandshake.PROTOCOL_VERSION)
        ok, err = hs.process_version(bad)
        self.assertFalse(ok)
        self.assertIn("Wrong network", str(err))

    def test_wrong_network_magic_is_rejected_both_directions(self) -> None:
        mainnet = MessageCodec("mainnet").encode("ping", b"\x00" * 8)
        regtest = MessageCodec("regtest").encode("ping", b"\x00" * 8)
        with self.assertRaises(ValueError):
            MessageCodec("regtest").decode(mainnet)
        with self.assertRaises(ValueError):
            MessageCodec("mainnet").decode(regtest)

    def test_peer_uses_configured_network_codec(self) -> None:
        p = Peer("127.0.0.1", 8333, is_outbound=True)
        p.configure_handshake(network="regtest", node_id="n1", start_height=7, best_block_hash="aa" * 32)
        self.assertEqual(p.codec.network, "regtest")
        self.assertEqual(p.handshake.expected_network, "regtest")
        self.assertEqual(p.handshake.start_height, 7)
        self.assertEqual(p.handshake.node_id, "n1")

    def test_wrong_network_frame_disconnects_before_relay(self) -> None:
        async def run():
            peer = Peer("127.0.0.1", 8333, is_outbound=False)
            peer.configure_handshake(network="regtest")
            bad_msg = MessageCodec("mainnet").encode("tx", b"\x01\x02")
            peer.reader = _Reader([bad_msg[:24], bad_msg[24:]])
            peer.writer = _Writer()
            peer.connected = True
            peer.on_message = AsyncMock()
            peer.on_protocol_violation = AsyncMock()
            await peer._handle_messages()
            peer.on_message.assert_not_awaited()
            peer.on_protocol_violation.assert_awaited()

        asyncio.run(run())

    def test_oversized_payload_rejected(self) -> None:
        async def run():
            peer = Peer("127.0.0.1", 8333, is_outbound=False)
            peer.configure_handshake(network="regtest")
            cmd = b"tx".ljust(12, b"\x00")
            hdr = b"\xfa\xbf\xb5\xda" + cmd + struct.pack("<I", peer.MAX_PAYLOAD_SIZE + 1) + (b"\x00" * 4)
            peer.reader = _Reader([hdr])
            peer.writer = _Writer()
            peer.connected = True
            peer.on_protocol_violation = AsyncMock()
            await peer._handle_messages()
            peer.on_protocol_violation.assert_awaited()

        asyncio.run(run())

    def test_invalid_command_rejected(self) -> None:
        async def run():
            peer = Peer("127.0.0.1", 8333, is_outbound=False)
            peer.configure_handshake(network="regtest")
            msg = MessageCodec("regtest").encode("evilcmd", b"")
            peer.reader = _Reader([msg[:24]])
            peer.writer = _Writer()
            peer.connected = True
            peer.on_protocol_violation = AsyncMock()
            await peer._handle_messages()
            peer.on_protocol_violation.assert_awaited()

        asyncio.run(run())

    def test_handshake_timeout_fails_cleanly(self) -> None:
        async def run():
            peer = Peer("127.0.0.1", 8333, is_outbound=True)
            peer.configure_handshake(network="regtest")
            peer.send_message = AsyncMock()  # type: ignore[method-assign]
            peer._wait_for_message = AsyncMock(return_value=None)  # type: ignore[method-assign]
            ok = await peer._handshake()
            self.assertFalse(ok)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
