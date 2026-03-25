"""Serialization helpers for BerzCoin."""

import struct
from typing import List, Tuple, Union, Any
from .types import VarInt

class Serializer:
    """Serialization utilities for Bitcoin data structures."""

    @staticmethod
    def read_bytes(data: bytes, offset: int, length: int) -> Tuple[bytes, int]:
        """Read fixed number of bytes.

        Args:
            data: Source bytes
            offset: Current offset
            length: Number of bytes to read

        Returns:
            Tuple of (read bytes, new offset)
        """
        if offset + length > len(data):
            raise ValueError(f"Not enough data: need {length}, have {len(data) - offset}")
        return data[offset:offset + length], offset + length

    @staticmethod
    def read_uint8(data: bytes, offset: int) -> Tuple[int, int]:
        """Read uint8 (1 byte)."""
        value, = struct.unpack_from('<B', data, offset)
        return value, offset + 1

    @staticmethod
    def read_uint16(data: bytes, offset: int) -> Tuple[int, int]:
        """Read uint16 (2 bytes, little-endian)."""
        value, = struct.unpack_from('<H', data, offset)
        return value, offset + 2

    @staticmethod
    def read_uint32(data: bytes, offset: int) -> Tuple[int, int]:
        """Read uint32 (4 bytes, little-endian)."""
        value, = struct.unpack_from('<I', data, offset)
        return value, offset + 4

    @staticmethod
    def read_uint64(data: bytes, offset: int) -> Tuple[int, int]:
        """Read uint64 (8 bytes, little-endian)."""
        value, = struct.unpack_from('<Q', data, offset)
        return value, offset + 8

    @staticmethod
    def read_varint(data: bytes, offset: int) -> Tuple[int, int]:
        """Read variable length integer."""
        value, consumed = VarInt.decode(data, offset)
        return value, offset + consumed

    @staticmethod
    def read_string(data: bytes, offset: int) -> Tuple[str, int]:
        """Read variable length string."""
        length, offset = Serializer.read_varint(data, offset)
        string_bytes, offset = Serializer.read_bytes(data, offset, length)
        return string_bytes.decode('utf-8'), offset

    @staticmethod
    def write_uint8(value: int) -> bytes:
        """Write uint8."""
        return struct.pack('<B', value)

    @staticmethod
    def write_uint16(value: int) -> bytes:
        """Write uint16 (little-endian)."""
        return struct.pack('<H', value)

    @staticmethod
    def write_uint32(value: int) -> bytes:
        """Write uint32 (little-endian)."""
        return struct.pack('<I', value)

    @staticmethod
    def write_uint64(value: int) -> bytes:
        """Write uint64 (little-endian)."""
        return struct.pack('<Q', value)

    @staticmethod
    def write_varint(value: int) -> bytes:
        """Write variable length integer."""
        return VarInt.encode(value)

    @staticmethod
    def write_string(value: str) -> bytes:
        """Write variable length string."""
        string_bytes = value.encode('utf-8')
        return VarInt.encode(len(string_bytes)) + string_bytes

    @staticmethod
    def write_bytes(data: bytes) -> bytes:
        """Write variable length bytes."""
        return VarInt.encode(len(data)) + data

class Deserializable:
    """Base class for deserializable objects."""

    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple[Any, int]:
        """Deserialize from bytes.

        Args:
            data: Source bytes
            offset: Starting offset

        Returns:
            Tuple of (deserialized object, new offset)
        """
        raise NotImplementedError

    def serialize(self) -> bytes:
        """Serialize to bytes."""
        raise NotImplementedError

class Serializable:
    """Base class for serializable objects."""

    def serialize(self) -> bytes:
        """Serialize to bytes."""
        raise NotImplementedError
