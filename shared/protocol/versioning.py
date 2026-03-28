"""Version handshake management."""

import time
import secrets
from typing import Optional, Tuple
from shared.protocol.messages import VersionMessage, VerackMessage
from shared.utils.logging import get_logger

logger = get_logger()

class VersionHandshake:
    """Version handshake state machine."""

    PROTOCOL_VERSION = 70015
    MIN_PROTOCOL_VERSION = 70001

    def __init__(self, local_services: int = 1, user_agent: str = "/BerzCoin:1.0/",
                 start_height: int = 0, relay: bool = True):
        self.local_services = local_services
        self.user_agent = user_agent
        self.start_height = start_height
        self.relay = relay
        self.nonce = secrets.randbits(64)

        self.remote_version: Optional[VersionMessage] = None
        self.verack_sent = False
        self.verack_received = False

    def create_version(self, remote_ip: bytes = b"\x00" * 16, remote_port: int = 8333,
                       local_ip: bytes = b"\x00" * 16, local_port: int = 8333) -> VersionMessage:
        return VersionMessage(
            version=self.PROTOCOL_VERSION,
            services=self.local_services,
            timestamp=int(time.time()),
            addr_recv_services=1,
            addr_recv_ip=remote_ip,
            addr_recv_port=remote_port,
            addr_from_services=self.local_services,
            addr_from_ip=local_ip,
            addr_from_port=local_port,
            nonce=self.nonce,
            user_agent=self.user_agent,
            start_height=self.start_height,
            relay=self.relay,
        )

    def process_version(self, version: VersionMessage) -> Tuple[bool, Optional[str]]:
        self.remote_version = version
        if version.version < self.MIN_PROTOCOL_VERSION:
            return False, f"Protocol version too old: {version.version}"
        if version.version > self.PROTOCOL_VERSION:
            logger.warning(f"Peer version {version.version} newer than ours")
        logger.info(
            f"Peer version: {version.version}, user agent: {version.user_agent}, "
            f"height: {version.start_height}"
        )
        return True, None

    def create_verack(self) -> VerackMessage:
        self.verack_sent = True
        return VerackMessage()

    def process_verack(self) -> bool:
        self.verack_received = True
        return self.is_complete()

    def is_complete(self) -> bool:
        return self.verack_sent and self.verack_received

    def get_remote_height(self) -> int:
        return self.remote_version.start_height if self.remote_version else 0

    def get_remote_services(self) -> int:
        return self.remote_version.services if self.remote_version else 0

    def is_witness_enabled(self) -> bool:
        if self.remote_version:
            return (self.remote_version.services & (1 << 5)) != 0
        return False

    def reset(self) -> None:
        self.remote_version = None
        self.verack_sent = False
        self.verack_received = False
        self.nonce = secrets.randbits(64)

class PeerVersion:
    """Peer version information."""

    def __init__(self, version: VersionMessage):
        self.version = version
        self.connected_at = int(time.time())
        self.ping_time: Optional[int] = None
        self.ping_rtt: Optional[int] = None

    @property
    def user_agent(self) -> str:
        return self.version.user_agent

    @property
    def start_height(self) -> int:
        return self.version.start_height

    @property
    def services(self) -> int:
        return self.version.services

    @property
    def protocol_version(self) -> int:
        return self.version.version

    def is_synced(self, current_height: int) -> bool:
        return abs(self.start_height - current_height) <= 10

    def __repr__(self) -> str:
        return f"PeerVersion(v{self.protocol_version}, {self.user_agent}, height={self.start_height})"
