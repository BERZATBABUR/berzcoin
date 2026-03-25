"""Hashing functions for BerzCoin."""

import hashlib
from typing import Union

def sha256(data: Union[bytes, bytearray]) -> bytes:
    """Calculate SHA256 hash.

    Args:
        data: Input data

    Returns:
        32-byte SHA256 hash
    """
    return hashlib.sha256(data).digest()

def ripemd160(data: Union[bytes, bytearray]) -> bytes:
    """Calculate RIPEMD160 hash.

    Args:
        data: Input data

    Returns:
        20-byte RIPEMD160 hash
    """
    return hashlib.new('ripemd160', data).digest()

def hash256(data: Union[bytes, bytearray]) -> bytes:
    """Calculate double SHA256 (SHA256(SHA256(data))).

    Args:
        data: Input data

    Returns:
        32-byte double SHA256 hash
    """
    return sha256(sha256(data))

def hash160(data: Union[bytes, bytearray]) -> bytes:
    """Calculate RIPEMD160(SHA256(data)).

    Args:
        data: Input data

    Returns:
        20-byte hash160
    """
    return ripemd160(sha256(data))

def sha256d(data: Union[bytes, bytearray]) -> bytes:
    """Alias for hash256."""
    return hash256(data)

def tagged_hash(tag: str, data: Union[bytes, bytearray]) -> bytes:
    """Calculate tagged hash for Taproot (BIP340).

    Args:
        tag: Tag string
        data: Input data

    Returns:
        32-byte tagged hash
    """
    tag_hash = sha256(tag.encode())
    return sha256(tag_hash + tag_hash + data)

class Hash:
    """Hash utilities class."""

    @staticmethod
    def sha256(data: bytes) -> bytes:
        """SHA256 hash."""
        return sha256(data)

    @staticmethod
    def ripemd160(data: bytes) -> bytes:
        """RIPEMD160 hash."""
        return ripemd160(data)

    @staticmethod
    def hash256(data: bytes) -> bytes:
        """Double SHA256."""
        return hash256(data)

    @staticmethod
    def hash160(data: bytes) -> bytes:
        """Hash160 (RIPEMD160 of SHA256)."""
        return hash160(data)

    @staticmethod
    def merkle_root(hashes: list) -> bytes:
        """Calculate Merkle root from list of hashes."""
        if not hashes:
            return b'\x00' * 32

        if len(hashes) == 1:
            return hashes[0]

        # Duplicate last hash if odd number
        if len(hashes) % 2 == 1:
            hashes.append(hashes[-1])

        # Calculate next level
        next_level = []
        for i in range(0, len(hashes), 2):
            combined = hashes[i] + hashes[i + 1]
            next_level.append(hash256(combined))

        return Hash.merkle_root(next_level)
