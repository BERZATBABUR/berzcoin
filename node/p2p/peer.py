"""Peer connection management."""

import asyncio
import socket
import time
from typing import Optional, Callable, Dict, Any, List
from shared.protocol.codec import MessageCodec
from shared.protocol.messages import *
from shared.protocol.versioning import VersionHandshake, PeerVersion
from shared.utils.logging import get_logger
from shared.utils.errors import ProtocolError

logger = get_logger()

class Peer:
    """Peer connection handler."""
    MAX_PAYLOAD_SIZE = 2_000_000

    def __init__(self, host: str, port: int, is_outbound: bool = True):
        self.host = host
        self.port = port
        self.is_outbound = is_outbound
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.codec = MessageCodec()
        self.handshake = VersionHandshake()
        self.version: Optional[PeerVersion] = None
        self.connected = False
        self.connected_at = 0.0
        self.connecting = False
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.relay_txs: bool = True
        self.prefers_compact_blocks: bool = False
        self.compact_block_version: int = 0
        self.compact_successes: int = 0
        self.compact_failures: int = 0
        self.last_message_at: float = 0.0

    async def connect(self) -> bool:
        if self.connecting or self.connected:
            return False
        self.connecting = True
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10,
            )
            if not await self._handshake():
                await self.disconnect()
                return False
            self.connected = True
            self.connected_at = asyncio.get_event_loop().time()
            self.last_message_at = self.connected_at
            self.connecting = False
            logger.info(f"Connected to {self.host}:{self.port}")
            asyncio.create_task(self._handle_messages())
            return True
        except Exception as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self.connecting = False
            return False

    async def _handshake(self) -> bool:
        version_msg = self.handshake.create_version()
        await self.send_message("version", version_msg.serialize())
        version_response = await self._wait_for_message("version", timeout=30)
        if version_response is None:
            logger.error(f"Timeout waiting for version from {self.host}")
            return False
        remote_version, _ = VersionMessage.deserialize(version_response)
        valid, error = self.handshake.process_version(remote_version)
        if not valid:
            logger.error(f"Invalid version from {self.host}: {error}")
            return False
        self.relay_txs = bool(getattr(remote_version, "relay", True))
        verack_msg = self.handshake.create_verack()
        await self.send_message("verack", verack_msg.serialize())
        verack_response = await self._wait_for_message("verack", timeout=30)
        if verack_response is None:
            logger.error(f"Timeout waiting for verack from {self.host}")
            return False
        self.handshake.process_verack()
        self.version = PeerVersion(remote_version)
        # Negotiate compact block announcements (version 1 envelope for now).
        await self.send_sendcmpct(announce=True, version=1)
        logger.info(f"Handshake complete with {self.host}")
        return True

    async def _wait_for_message(self, command: str, timeout: int = 30) -> Optional[bytes]:
        try:
            while True:
                header_data = await asyncio.wait_for(self.reader.readexactly(24), timeout)
                payload_len = int.from_bytes(header_data[16:20], 'little')
                if payload_len > self.MAX_PAYLOAD_SIZE:
                    logger.warning("Rejecting oversized payload from %s", self.host)
                    return None
                cmd = header_data[4:16].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
                if cmd == command:
                    payload = await self.reader.readexactly(payload_len)
                    return payload
                if payload_len:
                    await self.reader.readexactly(payload_len)
        except asyncio.TimeoutError:
            return None
        except asyncio.IncompleteReadError:
            return None
        except Exception as e:
            logger.error(f"Error waiting for message: {e}")
            return None

    async def _handle_messages(self) -> None:
        try:
            while self.connected:
                header = await self.reader.readexactly(24)
                command = header[4:16].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
                payload_len = int.from_bytes(header[16:20], 'little')
                if payload_len > self.MAX_PAYLOAD_SIZE:
                    logger.warning("Peer %s sent oversized payload (%s)", self.host, payload_len)
                    break
                payload = await self.reader.readexactly(payload_len) if payload_len else b""
                self.last_message_at = asyncio.get_event_loop().time()
                if self.on_message:
                    await self.on_message(self, command, payload)
        except asyncio.IncompleteReadError:
            pass
        except Exception as e:
            logger.error(f"Error handling messages from {self.host}: {e}")
        finally:
            await self.disconnect()

    async def send_message(self, command: str, payload: bytes) -> None:
        # Handshake messages (version/verack) are sent before `connected=True`,
        # so only require a live writer here.
        if not self.writer:
            return
        try:
            encoded = self.codec.encode(command, payload)
            self.writer.write(encoded)
            await self.writer.drain()
        except Exception as e:
            logger.error(f"Failed to send message to {self.host}: {e}")
            await self.disconnect()

    async def send_version(self) -> None:
        version_msg = self.handshake.create_version()
        await self.send_message("version", version_msg.serialize())

    async def send_verack(self) -> None:
        verack_msg = self.handshake.create_verack()
        await self.send_message("verack", verack_msg.serialize())

    async def send_getaddr(self) -> None:
        await self.send_message("getaddr", b"")

    async def send_ping(self, nonce: int = None) -> None:
        if nonce is None:
            nonce = int(time.time())
        ping_msg = PingMessage(nonce)
        await self.send_message("ping", ping_msg.serialize())

    async def send_getheaders(self, locator_hashes: List[bytes], hash_stop: bytes = b"\x00" * 32) -> None:
        msg = GetHeadersMessage(block_locator_hashes=locator_hashes, hash_stop=hash_stop)
        await self.send_message("getheaders", msg.serialize())

    async def send_getdata(self, inv_type: int, inv_hash: bytes) -> None:
        msg = GetDataMessage(inventory=[(inv_type, inv_hash)])
        await self.send_message("getdata", msg.serialize())

    async def send_sendcmpct(self, announce: bool = True, version: int = 1) -> None:
        msg = SendCmpctMessage(announce=announce, version=version)
        await self.send_message("sendcmpct", msg.serialize())

    async def send_cmpctblock(self, message: CmpctBlockMessage) -> None:
        await self.send_message("cmpctblock", message.serialize())

    async def send_getblocktxn(self, block_hash: bytes, indexes: List[int]) -> None:
        msg = GetBlockTxnMessage(block_hash=block_hash, indexes=list(indexes))
        await self.send_message("getblocktxn", msg.serialize())

    async def send_blocktxn(self, block_hash: bytes, transactions: List[bytes]) -> None:
        msg = BlockTxnMessage(block_hash=block_hash, transactions=list(transactions))
        await self.send_message("blocktxn", msg.serialize())

    def record_compact_result(self, success: bool) -> None:
        if success:
            self.compact_successes += 1
            if self.compact_failures > 0:
                self.compact_failures -= 1
            return
        self.compact_failures += 1
        # Auto-downgrade peers repeatedly failing compact reconstruction.
        if self.compact_failures >= 3 and self.compact_failures > (self.compact_successes * 2):
            self.prefers_compact_blocks = False

    async def disconnect(self) -> None:
        was_connected = self.connected
        self.connected = False
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        if was_connected and self.on_disconnect:
            await self.on_disconnect(self)
        if was_connected:
            logger.info(f"Disconnected from {self.host}:{self.port}")

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def is_handshaked(self) -> bool:
        return self.handshake.is_complete()

    @property
    def peer_height(self) -> int:
        return self.handshake.get_remote_height() if self.handshake else 0

    def __repr__(self) -> str:
        return f"Peer({self.address}, outbound={self.is_outbound}, height={self.peer_height})"
