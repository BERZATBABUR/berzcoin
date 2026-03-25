"""secp256k1 elliptic curve implementation."""

import hashlib
import hmac
from typing import Tuple, Optional

# secp256k1 curve parameters
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

class Point:
    """Point on secp256k1 curve."""

    def __init__(self, x: Optional[int], y: Optional[int]):
        """Initialize point.

        Args:
            x: X coordinate (None for infinity point)
            y: Y coordinate (None for infinity point)
        """
        self.x = x
        self.y = y
        self.inf = (x is None or y is None)

    def __eq__(self, other: 'Point') -> bool:
        if self.inf and other.inf:
            return True
        if self.inf or other.inf:
            return False
        return self.x == other.x and self.y == other.y

    def __add__(self, other: 'Point') -> 'Point':
        """Point addition."""
        if self.inf:
            return other
        if other.inf:
            return self
        if self.x == other.x:
            if self.y == other.y:
                return self.double()
            else:
                return INFINITY

        # Calculate slope
        s = ((other.y - self.y) * pow((other.x - self.x) % P, P-2, P)) % P

        # Calculate new point
        x3 = (s*s - self.x - other.x) % P
        y3 = (s*(self.x - x3) - self.y) % P

        return Point(x3, y3)

    def double(self) -> 'Point':
        """Point doubling."""
        if self.inf:
            return INFINITY

        # Calculate slope
        s = ((3 * self.x * self.x) * pow((2 * self.y) % P, P-2, P)) % P

        # Calculate new point
        x3 = (s*s - 2*self.x) % P
        y3 = (s*(self.x - x3) - self.y) % P

        return Point(x3, y3)

    def multiply(self, scalar: int) -> 'Point':
        """Scalar multiplication using double-and-add algorithm."""
        result = INFINITY
        current = self

        while scalar > 0:
            if scalar & 1:
                result = result + current
            current = current.double()
            scalar >>= 1

        return result

# Infinity point
INFINITY = Point(None, None)
GENERATOR = Point(Gx, Gy)

def private_to_public(private_key: int) -> Tuple[int, int]:
    """Convert private key to public key coordinates.

    Args:
        private_key: Private key integer

    Returns:
        Tuple of (x, y) coordinates
    """
    point = GENERATOR.multiply(private_key)
    return (point.x, point.y)

def sign_message(private_key: int, message_hash: bytes) -> Tuple[int, int]:
    """Sign a message hash with private key.

    Args:
        private_key: Private key integer
        message_hash: 32-byte message hash

    Returns:
        Tuple of (r, s) signature components
    """
    # Simplified deterministic k generation (RFC 6979 style)
    # In production, use proper RFC 6979 implementation
    import secrets
    k = secrets.randbelow(N)

    # Calculate r
    point = GENERATOR.multiply(k)
    r = point.x % N
    if r == 0:
        return sign_message(private_key, message_hash)

    # Calculate s
    msg_int = int.from_bytes(message_hash, 'big')
    s = (pow(k, N-2, N) * (msg_int + private_key * r)) % N
    if s == 0:
        return sign_message(private_key, message_hash)

    return (r, s)

def verify_signature(public_key: Tuple[int, int], message_hash: bytes, signature: Tuple[int, int]) -> bool:
    """Verify a signature.

    Args:
        public_key: Tuple of (x, y) coordinates
        message_hash: 32-byte message hash
        signature: Tuple of (r, s) components

    Returns:
        True if signature is valid
    """
    r, s = signature

    if r <= 0 or r >= N or s <= 0 or s >= N:
        return False

    # Compute w = s^-1 mod N
    w = pow(s, N-2, N)

    # Compute u1 = (msg * w) mod N
    msg_int = int.from_bytes(message_hash, 'big')
    u1 = (msg_int * w) % N

    # Compute u2 = (r * w) mod N
    u2 = (r * w) % N

    # Compute point = u1*G + u2*P
    point = GENERATOR.multiply(u1)
    point = point + Point(public_key[0], public_key[1]).multiply(u2)

    if point.inf:
        return False

    return point.x % N == r
