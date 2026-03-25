"""P2P message serialization/deserialization."""

import struct
from typing import Tuple, Any, Dict
from shared.core.serialization import Serializer
from shared.core.hashes import hash256

class MessageHeader:
    """P2P message header."""

    MAGIC_BYTES = {
        "mainnet": b"\xf9\xbe\xb4\xd9",
        "testnet": b"\x0b\x11\x09\x07",
        "regtest": b"\xfa\xbf\xb5\xda",
    }

    def __init__(self, command: str, payload: bytes, network: str = 'mainnet'):
        """Initialize message header.

        Args:
            command: Message command (12 bytes, null-padded)
            payload: Message payload
            network: Network (mainnet, testnet, regtest)
        """
        self.magic = self.MAGIC_BYTES[network]
        self.command = command.encode("ascii", errors="replace")[:12].ljust(12, b"\x00")
        self.length = len(payload)
        self.checksum = hash256(payload)[:4]
        self.payload = payload

    def serialize(self) -> bytes:
        """Serialize message header + payload.

        Returns:
            Complete message bytes
        """
        result = self.magic
        result += self.command
        result += struct.pack('<I', self.length)
        result += self.checksum
        result += self.payload
        return result

    @classmethod
    def deserialize(cls, data: bytes, network: str = 'mainnet') -> Tuple['MessageHeader', int]:
        """Deserialize message from bytes.

        Args:
            data: Source bytes
            network: Expected network

        Returns:
            Tuple of (MessageHeader, bytes consumed)
        """
        offset = 0

        # Read magic
        magic = data[offset:offset+4]
        offset += 4

        if magic != cls.MAGIC_BYTES[network]:
            raise ValueError(f"Invalid magic bytes: {magic.hex()}")

        # Read command
        command = data[offset : offset + 12].decode("ascii", errors="replace").rstrip("\x00")
        offset += 12

        # Read length
        length, = struct.unpack_from('<I', data, offset)
        offset += 4

        # Read checksum
        checksum = data[offset:offset+4]
        offset += 4

        # Read payload
        if offset + length > len(data):
            raise ValueError(f"Not enough data for payload: need {length}, have {len(data) - offset}")
        payload = data[offset:offset+length]
        offset += length

        # Verify checksum
        calculated = hash256(payload)[:4]
        if checksum != calculated:
            raise ValueError(f"Invalid checksum: expected {calculated.hex()}, got {checksum.hex()}")

        header = cls(command, payload, network)
        header.checksum = checksum
        return header, offset

class MessageCodec:
    """P2P message encoding/decoding."""

    def __init__(self, network: str = 'mainnet'):
        """Initialize codec.

        Args:
            network: Network (mainnet, testnet, regtest)
        """
        self.network = network

    def encode(self, command: str, payload: bytes) -> bytes:
        """Encode message.

        Args:
            command: Message command
            payload: Message payload

        Returns:
            Encoded message bytes
        """
        header = MessageHeader(command, payload, self.network)
        return header.serialize()

    def decode(self, data: bytes) -> Tuple[str, bytes, int]:
        """Decode message.

        Args:
            data: Raw message bytes

        Returns:
            Tuple of (command, payload, bytes consumed)
        """
        header, consumed = MessageHeader.deserialize(data, self.network)
        return header.command, header.payload, consumed

    def encode_version(self, version: int, services: int, timestamp: int,
                       addr_recv_services: int, addr_recv_ip: bytes, addr_recv_port: int,
                       addr_from_services: int, addr_from_ip: bytes, addr_from_port: int,
                       nonce: int, user_agent: str, start_height: int, relay: bool = True) -> bytes:
        """Encode version message.

        Args:
            version: Protocol version
            services: Local services
            timestamp: Current timestamp
            addr_recv_services: Receiver services
            addr_recv_ip: Receiver IP (16 bytes)
            addr_recv_port: Receiver port
            addr_from_services: Sender services
            addr_from_ip: Sender IP (16 bytes)
            addr_from_port: Sender port
            nonce: Random nonce
            user_agent: User agent string
            start_height: Current block height
            relay: Relay flag (BIP37)

        Returns:
            Encoded version message payload
        """
        payload = b''
        payload += struct.pack('<i', version)
        payload += struct.pack('<Q', services)
        payload += struct.pack('<q', timestamp)

        # Receiver address
        payload += struct.pack('<Q', addr_recv_services)
        payload += addr_recv_ip
        payload += struct.pack('>H', addr_recv_port)

        # Sender address
        payload += struct.pack('<Q', addr_from_services)
        payload += addr_from_ip
        payload += struct.pack('>H', addr_from_port)

        payload += struct.pack('<Q', nonce)
        payload += Serializer.write_string(user_agent)
        payload += struct.pack('<i', start_height)
        payload += struct.pack('<?', relay)

        return payload

    def decode_version(self, data: bytes) -> Dict[str, Any]:
        """Decode version message.

        Args:
            data: Version message payload

        Returns:
            Dictionary of decoded fields
        """
        offset = 0
        result = {}

        result['version'], offset = Serializer.read_int32(data, offset)
        result['services'], offset = Serializer.read_uint64(data, offset)
        result['timestamp'], offset = Serializer.read_int64(data, offset)

        # Receiver address
        result['addr_recv_services'], offset = Serializer.read_uint64(data, offset)
        result['addr_recv_ip'], offset = Serializer.read_bytes(data, offset, 16)
        result['addr_recv_port'], = struct.unpack_from('>H', data, offset)
        offset += 2

        # Sender address
        result['addr_from_services'], offset = Serializer.read_uint64(data, offset)
        result['addr_from_ip'], offset = Serializer.read_bytes(data, offset, 16)
        result['addr_from_port'], = struct.unpack_from('>H', data, offset)
        offset += 2

        result['nonce'], offset = Serializer.read_uint64(data, offset)
        result['user_agent'], offset = Serializer.read_string(data, offset)
        result['start_height'], offset = Serializer.read_int32(data, offset)

        if offset < len(data):
            result['relay'], offset = Serializer.read_bool(data, offset)
        else:
            result['relay'] = True

        return result
