"""Peer connection management."""

import asyncio
import socket
import time
from typing import Optional, Callable, Dict, Any
from shared.protocol.codec import MessageCodec
from shared.protocol.messages import *
from shared.protocol.versioning import VersionHandshake, PeerVersion
from shared.utils.logging import get_logger
from shared.utils.errors import ProtocolError

logger = get_logger()

class Peer:
    """Peer connection handler."""

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
        self.connecting = False
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None

    async def connect(self) -> bool:
        if self.connecting or self.connected:
            return False
        self.connecting = True
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            if not await self._handshake():
                await self.disconnect()
                return False
            self.connected = True
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
        if not version_response:
            logger.error(f"Timeout waiting for version from {self.host}")
            return False
        remote_version, _ = VersionMessage.deserialize(version_response)
        valid, error = self.handshake.process_version(remote_version)
        if not valid:
            logger.error(f"Invalid version from {self.host}: {error}")
            return False
        verack_msg = self.handshake.create_verack()
        await self.send_message("verack", verack_msg.serialize())
        verack_response = await self._wait_for_message("verack", timeout=30)
        if not verack_response:
            logger.error(f"Timeout waiting for verack from {self.host}")
            return False
        self.handshake.process_verack()
        self.version = PeerVersion(remote_version)
        logger.info(f"Handshake complete with {self.host}")
        return True

    async def _wait_for_message(self, command: str, timeout: int = 30) -> Optional[bytes]:
        try:
            while True:
                header_data = await asyncio.wait_for(self.reader.read(24), timeout)
                if not header_data:
                    return None
                if command in header_data.decode('ascii', errors='ignore'):
                    payload_len = int.from_bytes(header_data[16:20], 'little')
                    payload = await self.reader.read(payload_len)
                    return payload
                payload_len = int.from_bytes(header_data[16:20], 'little')
                await self.reader.read(payload_len)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error waiting for message: {e}")
            return None

    async def _handle_messages(self) -> None:
        try:
            while self.connected:
                header = await self.reader.read(24)
                if len(header) < 24:
                    break
                command = header[4:16].decode('ascii').strip('')
                payload_len = int.from_bytes(header[16:20], 'little')
                payload = await self.reader.read(payload_len)
                if self.on_message:
                    await self.on_message(self, command, payload)
        except Exception as e:
            logger.error(f"Error handling messages from {self.host}: {e}")
        finally:
            await self.disconnect()

    async def send_message(self, command: str, payload: bytes) -> None:
        if not self.writer or not self.connected:
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

    async def send_getheaders(self, locator_hashes: List[bytes], hash_stop: bytes = b'' * 32) -> None:
        msg = GetHeadersMessage(block_locator_hashes=locator_hashes, hash_stop=hash_stop)
        await self.send_message("getheaders", msg.serialize())

    async def send_getdata(self, inv_type: int, inv_hash: bytes) -> None:
        msg = GetDataMessage(inventory=[(inv_type, inv_hash)])
        await self.send_message("getdata", msg.serialize())

    async def disconnect(self) -> None:
        if not self.connected:
            return
        self.connected = False
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        if self.on_disconnect:
            await self.on_disconnect(self)
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
