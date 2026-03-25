"""Signature creation and verification."""

import hashlib
from typing import Tuple
from .keys import PrivateKey, PublicKey

def sign_message_hash(private_key: PrivateKey, message_hash: bytes) -> bytes:
    """Sign a message hash and return DER encoded signature.

    Args:
        private_key: Private key to sign with
        message_hash: 32-byte message hash

    Returns:
        DER encoded signature
    """
    r, s = private_key.sign(message_hash)

    # DER encoding
    def encode_int(num: int) -> bytes:
        """Encode integer for DER."""
        if num == 0:
            return b"\x00"

        # Convert to bytes
        num_bytes = num.to_bytes((num.bit_length() + 7) // 8, "big")

        # Add leading zero if high bit is set
        if num_bytes[0] & 0x80:
            num_bytes = b"\x00" + num_bytes

        return num_bytes

    r_bytes = encode_int(r)
    s_bytes = encode_int(s)

    # Construct DER signature
    der = b"\x30"
    der += bytes([len(r_bytes) + len(s_bytes) + 4])
    der += b"\x02"
    der += bytes([len(r_bytes)])
    der += r_bytes
    der += b"\x02"
    der += bytes([len(s_bytes)])
    der += s_bytes

    return der

def verify_signature(public_key: PublicKey, message_hash: bytes, signature: bytes) -> bool:
    """Verify a DER encoded signature.

    Args:
        public_key: Public key to verify with
        message_hash: 32-byte message hash
        signature: DER encoded signature

    Returns:
        True if signature is valid
    """
    # Parse DER signature
    if signature[0] != 0x30:
        return False

    # Skip length
    pos = 2

    # Parse r
    if signature[pos] != 0x02:
        return False
    pos += 1

    r_len = signature[pos]
    pos += 1
    r_bytes = signature[pos:pos + r_len]
    pos += r_len

    # Parse s
    if signature[pos] != 0x02:
        return False
    pos += 1

    s_len = signature[pos]
    pos += 1
    s_bytes = signature[pos:pos + s_len]

    # Convert to integers
    r = int.from_bytes(r_bytes, 'big')
    s = int.from_bytes(s_bytes, 'big')

    return public_key.verify(message_hash, (r, s))
