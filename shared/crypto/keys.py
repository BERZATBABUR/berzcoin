"""Key generation and management."""

import secrets
import hashlib
from typing import Tuple, Optional
from .base58 import base58_check_encode, base58_check_decode
from .secp256k1 import N as SECP256K1_ORDER
from .secp256k1 import private_to_public, sign_message, verify_signature

class PrivateKey:
    """Bitcoin private key."""

    def __init__(self, key: Optional[int] = None):
        """Initialize private key.

        Args:
            key: Optional private key integer (generates random if not provided)
        """
        if key is None:
            # Valid secp256k1 private keys are in [1, n-1].
            self.key = secrets.randbelow(SECP256K1_ORDER - 1) + 1
        else:
            key_int = int(key)
            if key_int <= 0 or key_int >= SECP256K1_ORDER:
                raise ValueError("Invalid private key range")
            self.key = key_int

    def to_int(self) -> int:
        """Convert to integer."""
        return self.key

    def to_hex(self) -> str:
        """Convert to hex string."""
        return hex(self.key)[2:].zfill(64)

    def to_wif(self, network: str = "mainnet", compressed: bool = True) -> str:
        """Encode private key as Wallet Import Format (WIF)."""
        version = b"\x80" if network == "mainnet" else b"\xef"
        payload = version + self.key.to_bytes(32, "big")
        if compressed:
            payload += b"\x01"
        return base58_check_encode(payload)

    @classmethod
    def from_wif(cls, wif: str) -> "PrivateKey":
        """Create private key from WIF string."""
        payload = base58_check_decode(wif)
        if len(payload) not in (33, 34):
            raise ValueError("Invalid WIF payload length")
        version = payload[0]
        if version not in (0x80, 0xEF):
            raise ValueError("Invalid WIF version")
        key_bytes = payload[1:33]
        if len(payload) == 34 and payload[33] != 0x01:
            raise ValueError("Invalid compressed WIF marker")
        return cls(int.from_bytes(key_bytes, "big"))

    def public_key(self) -> 'PublicKey':
        """Derive public key from private key."""
        x, y = private_to_public(self.key)
        return PublicKey(x, y)

    def sign(self, message_hash: bytes) -> Tuple[int, int]:
        """Sign a message hash.

        Args:
            message_hash: 32-byte message hash

        Returns:
            Signature tuple (r, s)
        """
        return sign_message(self.key, message_hash)

class PublicKey:
    """Bitcoin public key."""

    def __init__(self, x: int, y: int):
        """Initialize public key from coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        self.x = x
        self.y = y

    def to_bytes(self, compressed: bool = True) -> bytes:
        """Serialize public key.

        Args:
            compressed: Use compressed format (33 bytes) or uncompressed (65 bytes)

        Returns:
            Serialized public key bytes
        """
        if compressed:
            prefix = 0x02 if self.y % 2 == 0 else 0x03
            return bytes([prefix]) + self.x.to_bytes(32, 'big')
        else:
            prefix = 0x04
            return bytes([prefix]) + self.x.to_bytes(32, 'big') + self.y.to_bytes(32, 'big')

    @classmethod
    def from_bytes(cls, data: bytes) -> 'PublicKey':
        """Deserialize public key from bytes.

        Args:
            data: Serialized public key bytes

        Returns:
            PublicKey instance
        """
        if len(data) == 33:
            # Compressed key
            prefix = data[0]
            x = int.from_bytes(data[1:33], 'big')
            # Recover y from x
            from .secp256k1 import P
            y_squared = (pow(x, 3, P) + 7) % P
            y = pow(y_squared, (P + 1) // 4, P)
            if (prefix == 0x03 and y % 2 == 0) or (prefix == 0x02 and y % 2 == 1):
                y = P - y
            return cls(x, y)
        elif len(data) == 65:
            # Uncompressed key
            prefix = data[0]
            if prefix != 0x04:
                raise ValueError("Invalid public key prefix")
            x = int.from_bytes(data[1:33], 'big')
            y = int.from_bytes(data[33:65], 'big')
            return cls(x, y)
        else:
            raise ValueError(f"Invalid public key length: {len(data)}")

    def verify(self, message_hash: bytes, signature: Tuple[int, int]) -> bool:
        """Verify a signature.

        Args:
            message_hash: 32-byte message hash
            signature: Signature tuple (r, s)

        Returns:
            True if signature is valid
        """
        return verify_signature((self.x, self.y), message_hash, signature)
