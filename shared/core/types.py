"""Core type definitions for BerzCoin."""

from typing import Union, List, Tuple, Optional
import struct

class Uint256:
    """256-bit unsigned integer."""

    def __init__(self, value: Union[int, bytes, bytearray]):
        """Initialize Uint256.

        Args:
            value: Integer value or 32-byte representation
        """
        if isinstance(value, int):
            if value < 0:
                raise ValueError("Uint256 cannot be negative")
            self.value = value
        elif isinstance(value, (bytes, bytearray)):
            if len(value) != 32:
                raise ValueError(f"Expected 32 bytes, got {len(value)}")
            self.value = int.from_bytes(value, 'little')
        else:
            raise TypeError(f"Cannot create Uint256 from {type(value)}")

    def to_bytes(self) -> bytes:
        """Convert to 32-byte little-endian representation.

        Returns:
            32-byte representation
        """
        return self.value.to_bytes(32, 'little')

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other) -> bool:
        if isinstance(other, Uint256):
            return self.value == other.value
        return self.value == other

    def __lt__(self, other) -> bool:
        if isinstance(other, Uint256):
            return self.value < other.value
        return self.value < other

    def __le__(self, other) -> bool:
        if isinstance(other, Uint256):
            return self.value <= other.value
        return self.value <= other

    def __gt__(self, other) -> bool:
        if isinstance(other, Uint256):
            return self.value > other.value
        return self.value > other

    def __ge__(self, other) -> bool:
        if isinstance(other, Uint256):
            return self.value >= other.value
        return self.value >= other

    def __add__(self, other) -> 'Uint256':
        if isinstance(other, Uint256):
            return Uint256(self.value + other.value)
        return Uint256(self.value + other)

    def __sub__(self, other) -> 'Uint256':
        if isinstance(other, Uint256):
            return Uint256(self.value - other.value)
        return Uint256(self.value - other)

    def __repr__(self) -> str:
        return f"Uint256(0x{self.value:064x})"

class Uint160:
    """160-bit unsigned integer."""

    def __init__(self, value: Union[int, bytes, bytearray]):
        """Initialize Uint160.

        Args:
            value: Integer value or 20-byte representation
        """
        if isinstance(value, int):
            if value < 0:
                raise ValueError("Uint160 cannot be negative")
            self.value = value
        elif isinstance(value, (bytes, bytearray)):
            if len(value) != 20:
                raise ValueError(f"Expected 20 bytes, got {len(value)}")
            self.value = int.from_bytes(value, 'little')
        else:
            raise TypeError(f"Cannot create Uint160 from {type(value)}")

    def to_bytes(self) -> bytes:
        """Convert to 20-byte little-endian representation.

        Returns:
            20-byte representation
        """
        return self.value.to_bytes(20, 'little')

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other) -> bool:
        if isinstance(other, Uint160):
            return self.value == other.value
        return self.value == other

    def __repr__(self) -> str:
        return f"Uint160(0x{self.value:040x})"

class VarInt:
    """Variable-length integer encoding."""

    @staticmethod
    def encode(value: int) -> bytes:
        """Encode integer to varint format.

        Args:
            value: Integer to encode

        Returns:
            Varint encoded bytes
        """
        if value < 0:
            raise ValueError("Cannot encode negative varint")

        if value < 0xfd:
            return struct.pack('<B', value)
        elif value <= 0xffff:
            return bytes([0xFD]) + struct.pack('<H', value)
        elif value <= 0xffffffff:
            return bytes([0xFE]) + struct.pack('<I', value)
        else:
            return bytes([0xFF]) + struct.pack('<Q', value)

    @staticmethod
    def decode(data: bytes, offset: int = 0) -> Tuple[int, int]:
        """Decode varint from bytes.

        Args:
            data: Bytes containing varint
            offset: Starting offset

        Returns:
            Tuple of (decoded value, bytes consumed)
        """
        if offset >= len(data):
            raise ValueError("Insufficient data for varint")

        first = data[offset]

        if first < 0xfd:
            return first, 1
        elif first == 0xfd:
            if offset + 3 > len(data):
                raise ValueError("Insufficient data for varint")
            return struct.unpack('<H', data[offset+1:offset+3])[0], 3
        elif first == 0xfe:
            if offset + 5 > len(data):
                raise ValueError("Insufficient data for varint")
            return struct.unpack('<I', data[offset+1:offset+5])[0], 5
        else:  # 0xff
            if offset + 9 > len(data):
                raise ValueError("Insufficient data for varint")
            return struct.unpack('<Q', data[offset+1:offset+9])[0], 9
