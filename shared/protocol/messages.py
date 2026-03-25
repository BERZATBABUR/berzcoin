"""P2P message structures for BerzCoin."""

from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from shared.core.serialization import Serializer
from shared.core.hashes import hash256

@dataclass
class VersionMessage:
    """Version message (initial handshake)."""
    version: int = 70015
    services: int = 1
    timestamp: int = 0
    addr_recv_services: int = 1
    addr_recv_ip: bytes = field(default_factory=lambda: b"\x00" * 16)
    addr_recv_port: int = 8333
    addr_from_services: int = 1
    addr_from_ip: bytes = field(default_factory=lambda: b"\x00" * 16)
    addr_from_port: int = 8333
    nonce: int = 0
    user_agent: str = "/BerzCoin:1.0/"
    start_height: int = 0
    relay: bool = True

    def serialize(self) -> bytes:
        """Serialize version message."""
        result = Serializer.write_uint32(self.version)
        result += Serializer.write_uint64(self.services)
        result += Serializer.write_uint64(self.timestamp)

        result += Serializer.write_uint64(self.addr_recv_services)
        result += self.addr_recv_ip
        result += Serializer.write_uint16(self.addr_recv_port)

        result += Serializer.write_uint64(self.addr_from_services)
        result += self.addr_from_ip
        result += Serializer.write_uint16(self.addr_from_port)

        result += Serializer.write_uint64(self.nonce)
        result += Serializer.write_string(self.user_agent)
        result += Serializer.write_uint32(self.start_height)
        result += Serializer.write_uint8(1 if self.relay else 0)

        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['VersionMessage', int]:
        """Deserialize version message."""
        version, offset = Serializer.read_uint32(data, offset)
        services, offset = Serializer.read_uint64(data, offset)
        timestamp, offset = Serializer.read_uint64(data, offset)

        addr_recv_services, offset = Serializer.read_uint64(data, offset)
        addr_recv_ip, offset = Serializer.read_bytes(data, offset, 16)
        addr_recv_port, offset = Serializer.read_uint16(data, offset)

        addr_from_services, offset = Serializer.read_uint64(data, offset)
        addr_from_ip, offset = Serializer.read_bytes(data, offset, 16)
        addr_from_port, offset = Serializer.read_uint16(data, offset)

        nonce, offset = Serializer.read_uint64(data, offset)
        user_agent, offset = Serializer.read_string(data, offset)
        start_height, offset = Serializer.read_uint32(data, offset)
        relay, offset = Serializer.read_uint8(data, offset)

        return cls(
            version=version,
            services=services,
            timestamp=timestamp,
            addr_recv_services=addr_recv_services,
            addr_recv_ip=addr_recv_ip,
            addr_recv_port=addr_recv_port,
            addr_from_services=addr_from_services,
            addr_from_ip=addr_from_ip,
            addr_from_port=addr_from_port,
            nonce=nonce,
            user_agent=user_agent,
            start_height=start_height,
            relay=bool(relay)
        ), offset

@dataclass
class VerackMessage:
    """Verack message (acknowledge version)."""

    def serialize(self) -> bytes:
        """Serialize verack message."""
        return b''

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['VerackMessage', int]:
        """Deserialize verack message."""
        return cls(), offset

@dataclass
class GetHeadersMessage:
    """Get headers message."""
    version: int = 70015
    hash_count: int = 1
    block_locator_hashes: List[bytes] = field(default_factory=list)
    hash_stop: bytes = field(default_factory=lambda: b"\x00" * 32)

    def serialize(self) -> bytes:
        """Serialize getheaders message."""
        result = Serializer.write_uint32(self.version)
        result += Serializer.write_varint(len(self.block_locator_hashes))
        for hash_bytes in self.block_locator_hashes:
            result += hash_bytes
        result += self.hash_stop
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['GetHeadersMessage', int]:
        """Deserialize getheaders message."""
        version, offset = Serializer.read_uint32(data, offset)
        count, offset = Serializer.read_varint(data, offset)

        locator = []
        for _ in range(count):
            hash_bytes, offset = Serializer.read_bytes(data, offset, 32)
            locator.append(hash_bytes)

        hash_stop, offset = Serializer.read_bytes(data, offset, 32)

        return cls(version, count, locator, hash_stop), offset

@dataclass
class HeadersMessage:
    """Headers message."""
    headers: List[bytes] = field(default_factory=list)

    def serialize(self) -> bytes:
        """Serialize headers message."""
        result = Serializer.write_varint(len(self.headers))
        for header in self.headers:
            result += header
            result += b''
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['HeadersMessage', int]:
        """Deserialize headers message."""
        count, offset = Serializer.read_varint(data, offset)

        headers = []
        for _ in range(count):
            header, offset = Serializer.read_bytes(data, offset, 80)
            tx_count, offset = Serializer.read_varint(data, offset)
            headers.append(header)

        return cls(headers), offset

@dataclass
class GetBlocksMessage:
    """Get blocks message."""
    version: int = 70015
    block_locator_hashes: List[bytes] = field(default_factory=list)
    hash_stop: bytes = field(default_factory=lambda: b"\x00" * 32)

    def serialize(self) -> bytes:
        """Serialize getblocks message."""
        result = Serializer.write_uint32(self.version)
        result += Serializer.write_varint(len(self.block_locator_hashes))
        for hash_bytes in self.block_locator_hashes:
            result += hash_bytes
        result += self.hash_stop
        return result

@dataclass
class InvMessage:
    """Inventory message."""
    class InvType:
        ERROR = 0
        MSG_TX = 1
        MSG_BLOCK = 2
        MSG_FILTERED_BLOCK = 3
        MSG_CMPCT_BLOCK = 4
        MSG_WITNESS_TX = 0x40000001
        MSG_WITNESS_BLOCK = 0x40000002

    inventory: List[Tuple[int, bytes]] = field(default_factory=list)

    def serialize(self) -> bytes:
        """Serialize inv message."""
        result = Serializer.write_varint(len(self.inventory))
        for inv_type, inv_hash in self.inventory:
            result += Serializer.write_uint32(inv_type)
            result += inv_hash
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['InvMessage', int]:
        """Deserialize inv message."""
        count, offset = Serializer.read_varint(data, offset)

        inventory = []
        for _ in range(count):
            inv_type, offset = Serializer.read_uint32(data, offset)
            inv_hash, offset = Serializer.read_bytes(data, offset, 32)
            inventory.append((inv_type, inv_hash))

        return cls(inventory), offset

@dataclass
class GetDataMessage:
    """Get data message."""
    inventory: List[Tuple[int, bytes]] = field(default_factory=list)

    def serialize(self) -> bytes:
        """Serialize getdata message."""
        result = Serializer.write_varint(len(self.inventory))
        for inv_type, inv_hash in self.inventory:
            result += Serializer.write_uint32(inv_type)
            result += inv_hash
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['GetDataMessage', int]:
        """Deserialize getdata message."""
        count, offset = Serializer.read_varint(data, offset)

        inventory = []
        for _ in range(count):
            inv_type, offset = Serializer.read_uint32(data, offset)
            inv_hash, offset = Serializer.read_bytes(data, offset, 32)
            inventory.append((inv_type, inv_hash))

        return cls(inventory), offset

@dataclass
class BlockMessage:
    """Block message."""
    block: bytes = b''

    def serialize(self) -> bytes:
        return self.block

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['BlockMessage', int]:
        return cls(data[offset:]), len(data) - offset

@dataclass
class TxMessage:
    """Transaction message."""
    transaction: bytes = b''

    def serialize(self) -> bytes:
        return self.transaction

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['TxMessage', int]:
        return cls(data[offset:]), len(data) - offset

@dataclass
class AddrMessage:
    """Address message."""
    addresses: List[Dict[str, Any]] = field(default_factory=list)

    def serialize(self) -> bytes:
        result = Serializer.write_varint(len(self.addresses))
        for addr in self.addresses:
            result += Serializer.write_uint32(addr.get('time', 0))
            result += Serializer.write_uint64(addr.get('services', 1))
            result += addr.get("ip", b"\x00" * 16)
            result += Serializer.write_uint16(addr.get('port', 8333))
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['AddrMessage', int]:
        count, offset = Serializer.read_varint(data, offset)

        addresses = []
        for _ in range(count):
            timestamp, offset = Serializer.read_uint32(data, offset)
            services, offset = Serializer.read_uint64(data, offset)
            ip, offset = Serializer.read_bytes(data, offset, 16)
            port, offset = Serializer.read_uint16(data, offset)
            addresses.append({'time': timestamp, 'services': services, 'ip': ip, 'port': port})

        return cls(addresses), offset

@dataclass
class PingMessage:
    nonce: int = 0

    def serialize(self) -> bytes:
        return Serializer.write_uint64(self.nonce)

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['PingMessage', int]:
        nonce, offset = Serializer.read_uint64(data, offset)
        return cls(nonce), offset

@dataclass
class PongMessage:
    nonce: int = 0

    def serialize(self) -> bytes:
        return Serializer.write_uint64(self.nonce)

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['PongMessage', int]:
        nonce, offset = Serializer.read_uint64(data, offset)
        return cls(nonce), offset

@dataclass
class RejectMessage:
    message: str = ""
    code: int = 0
    reason: str = ""
    data: bytes = b''

    REJECT_MALFORMED = 0x01
    REJECT_INVALID = 0x10
    REJECT_OBSOLETE = 0x11
    REJECT_DUPLICATE = 0x12
    REJECT_NONSTANDARD = 0x40
    REJECT_DUST = 0x41
    REJECT_INSUFFICIENTFEE = 0x42
    REJECT_CHECKPOINT = 0x43

    def serialize(self) -> bytes:
        result = Serializer.write_string(self.message)
        result += Serializer.write_uint8(self.code)
        result += Serializer.write_string(self.reason)
        result += self.data
        return result

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['RejectMessage', int]:
        message, offset = Serializer.read_string(data, offset)
        code, offset = Serializer.read_uint8(data, offset)
        reason, offset = Serializer.read_string(data, offset)
        data_remaining = data[offset:]
        return cls(message, code, reason, data_remaining), len(data)
