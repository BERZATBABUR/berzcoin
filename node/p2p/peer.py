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
    VALID_COMMANDS = {
        "version", "verack", "ping", "pong", "inv", "getdata", "getheaders", "headers",
        "getblocks", "block", "tx", "addr", "getaddr", "sendcmpct", "cmpctblock",
        "getblocktxn", "blocktxn", "join_request", "join_challenge", "join_attest",
        "join_result",
    }

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
        self.on_protocol_violation: Optional[Callable[[Any, str], Any]] = None
        self.connect_timeout_secs: int = 10
        self.handshake_timeout_secs: int = 30
        self.read_timeout_secs: int = 30
        self.write_timeout_secs: int = 15
        self.idle_timeout_secs: int = 180
        self.partial_message_timeout_secs: int = 10
        self.min_read_progress_bytes: int = 1
        self.disconnect_reason: str = ""
        self.last_handshake_error: str = ""

    def configure_handshake(
        self,
        *,
        network: str,
        node_id: str = "",
        start_height: int = 0,
        best_block_hash: str = "",
        user_agent: str = "/BerzCoin:1.0/",
        services: int = 1,
        relay: bool = True,
        max_payload_size: Optional[int] = None,
        handshake_timeout_secs: Optional[int] = None,
        connect_timeout_secs: Optional[int] = None,
        read_timeout_secs: Optional[int] = None,
        write_timeout_secs: Optional[int] = None,
        idle_timeout_secs: Optional[int] = None,
        partial_message_timeout_secs: Optional[int] = None,
        min_read_progress_bytes: Optional[int] = None,
    ) -> None:
        """Bind codec+handshake fields to the node's active network/runtime identity."""
        self.codec = MessageCodec(str(network))
        self.handshake = VersionHandshake(
            local_services=int(services),
            user_agent=str(user_agent),
            start_height=int(start_height),
            relay=bool(relay),
            expected_network=str(network),
            node_id=str(node_id or ""),
            best_block_hash=str(best_block_hash or ""),
        )
        if max_payload_size is not None:
            self.MAX_PAYLOAD_SIZE = max(1024, int(max_payload_size))
        if handshake_timeout_secs is not None:
            self.handshake_timeout_secs = max(1, int(handshake_timeout_secs))
        if connect_timeout_secs is not None:
            self.connect_timeout_secs = max(1, int(connect_timeout_secs))
        if read_timeout_secs is not None:
            self.read_timeout_secs = max(1, int(read_timeout_secs))
        if write_timeout_secs is not None:
            self.write_timeout_secs = max(1, int(write_timeout_secs))
        if idle_timeout_secs is not None:
            self.idle_timeout_secs = max(1, int(idle_timeout_secs))
        if partial_message_timeout_secs is not None:
            self.partial_message_timeout_secs = max(1, int(partial_message_timeout_secs))
        if min_read_progress_bytes is not None:
            self.min_read_progress_bytes = max(1, int(min_read_progress_bytes))

    async def connect(self) -> bool:
        if self.connecting or self.connected:
            return False
        self.connecting = True
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=float(self.connect_timeout_secs),
            )
            if not await self._handshake():
                await self.disconnect(reason="handshake_failed")
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
        self.last_handshake_error = ""
        version_msg = self.handshake.create_version()
        await self.send_message("version", version_msg.serialize())
        version_response = await self._wait_for_message("version", timeout=self.handshake_timeout_secs)
        if version_response is None:
            if not self.last_handshake_error:
                self.last_handshake_error = "version_timeout"
            self.disconnect_reason = self.disconnect_reason or self.last_handshake_error
            logger.error(
                "Handshake failed with %s:%s waiting for version (%s)",
                self.host,
                self.port,
                self.last_handshake_error,
            )
            return False
        remote_version, _ = VersionMessage.deserialize(version_response)
        valid, error = self.handshake.process_version(remote_version)
        if not valid:
            self.last_handshake_error = f"invalid_version:{error}"
            self.disconnect_reason = self.disconnect_reason or self.last_handshake_error
            logger.error(f"Invalid version from {self.host}: {error}")
            return False
        self.relay_txs = bool(getattr(remote_version, "relay", True))
        verack_msg = self.handshake.create_verack()
        await self.send_message("verack", verack_msg.serialize())
        verack_response = await self._wait_for_message("verack", timeout=self.handshake_timeout_secs)
        if verack_response is None:
            if not self.last_handshake_error:
                self.last_handshake_error = "verack_timeout"
            self.disconnect_reason = self.disconnect_reason or self.last_handshake_error
            logger.error(
                "Handshake failed with %s:%s waiting for verack (%s)",
                self.host,
                self.port,
                self.last_handshake_error,
            )
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
                    await self._report_protocol_violation("oversized_payload")
                    return None
                payload = await self._read_exactly_with_progress(
                    payload_len,
                    timeout=max(1, int(timeout)),
                ) if payload_len else b""
                if payload is None:
                    await self._report_protocol_violation("partial_message_timeout")
                    return None
                try:
                    cmd, decoded_payload, _ = self.codec.decode(header_data + payload)
                except Exception as e:
                    self.last_handshake_error = f"malformed_message:{e}"
                    logger.warning(
                        "Malformed handshake message from %s:%s: %s",
                        self.host,
                        self.port,
                        e,
                    )
                    await self._report_protocol_violation("malformed_message")
                    return None
                if cmd not in self.VALID_COMMANDS:
                    self.last_handshake_error = f"invalid_command:{cmd}"
                    logger.warning(
                        "Invalid handshake command from %s:%s: %s",
                        self.host,
                        self.port,
                        cmd,
                    )
                    await self._report_protocol_violation("invalid_command")
                    return None
                if cmd == command:
                    return decoded_payload
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
                idle_budget = max(1, int(self.idle_timeout_secs))
                header = await asyncio.wait_for(
                    self.reader.readexactly(24),
                    timeout=float(idle_budget),
                )
                payload_len = int.from_bytes(header[16:20], 'little')
                if payload_len > self.MAX_PAYLOAD_SIZE:
                    logger.warning("Peer %s sent oversized payload (%s)", self.host, payload_len)
                    await self._report_protocol_violation("oversized_payload")
                    break
                payload = await self._read_exactly_with_progress(
                    payload_len,
                    timeout=max(1, int(self.read_timeout_secs)),
                ) if payload_len else b""
                if payload is None:
                    await self._report_protocol_violation("partial_message_timeout")
                    break
                try:
                    command, decoded_payload, _ = self.codec.decode(header + payload)
                except Exception:
                    await self._report_protocol_violation("malformed_message")
                    break
                if command not in self.VALID_COMMANDS:
                    await self._report_protocol_violation("invalid_command")
                    break
                self.last_message_at = asyncio.get_event_loop().time()
                if self.on_message:
                    await self.on_message(self, command, decoded_payload)
        except asyncio.TimeoutError:
            self.disconnect_reason = self.disconnect_reason or "idle_timeout"
        except asyncio.IncompleteReadError:
            self.disconnect_reason = self.disconnect_reason or "incomplete_read"
        except Exception as e:
            logger.error(f"Error handling messages from {self.host}: {e}")
            self.disconnect_reason = self.disconnect_reason or "read_error"
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
            await asyncio.wait_for(
                self.writer.drain(),
                timeout=float(max(1, int(self.write_timeout_secs))),
            )
        except asyncio.TimeoutError:
            self.disconnect_reason = self.disconnect_reason or "write_timeout"
            await self.disconnect(reason="write_timeout")
        except Exception as e:
            logger.error(f"Failed to send message to {self.host}: {e}")
            await self.disconnect(reason="send_error")

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

    async def send_join_request(self, message: JoinRequestMessage) -> None:
        await self.send_message("join_request", message.serialize())

    async def send_join_challenge(self, message: JoinChallengeMessage) -> None:
        await self.send_message("join_challenge", message.serialize())

    async def send_join_attest(self, message: JoinAttestMessage) -> None:
        await self.send_message("join_attest", message.serialize())

    async def send_join_result(self, message: JoinResultMessage) -> None:
        await self.send_message("join_result", message.serialize())

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

    async def disconnect(self, reason: str = "") -> None:
        was_connected = self.connected
        self.connected = False
        if reason:
            self.disconnect_reason = str(reason)
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        if was_connected and self.on_disconnect:
            await self.on_disconnect(self)
        if was_connected:
            logger.info(f"Disconnected from {self.host}:{self.port}")

    async def _report_protocol_violation(self, reason: str) -> None:
        self.disconnect_reason = self.disconnect_reason or str(reason)
        try:
            if self.on_protocol_violation:
                res = self.on_protocol_violation(self, str(reason))
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    async def _read_exactly_with_progress(self, total: int, timeout: int) -> Optional[bytes]:
        if total <= 0:
            return b""
        buf = bytearray()
        while len(buf) < total:
            chunk_budget = min(65536, total - len(buf))
            try:
                chunk = await asyncio.wait_for(
                    self.reader.read(chunk_budget),
                    timeout=float(max(1, int(timeout))),
                )
            except asyncio.TimeoutError:
                return None
            if not chunk:
                return None
            if len(chunk) < int(self.min_read_progress_bytes):
                wait_deadline = time.monotonic() + float(max(1, int(self.partial_message_timeout_secs)))
                while len(chunk) < int(self.min_read_progress_bytes):
                    if time.monotonic() >= wait_deadline:
                        return None
                    rest = await asyncio.wait_for(
                        self.reader.read(chunk_budget),
                        timeout=1.0,
                    )
                    if not rest:
                        return None
                    chunk += rest
                    if len(chunk) >= chunk_budget:
                        break
            buf.extend(chunk)
        return bytes(buf)

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
