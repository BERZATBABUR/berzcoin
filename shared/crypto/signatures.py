"""Signature creation and verification."""

from typing import Tuple
from .keys import PrivateKey, PublicKey
from .secp256k1 import schnorr_sign_message, schnorr_verify_message


CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _parse_der_signature_strict(signature: bytes) -> Tuple[int, int]:
    """Strict DER parser for ECDSA signatures (no trailing bytes, canonical integers)."""
    if not isinstance(signature, (bytes, bytearray)):
        raise ValueError("Signature must be bytes")
    sig = bytes(signature)
    # Bitcoin DER signatures are typically 8..72 bytes.
    if len(sig) < 8 or len(sig) > 72:
        raise ValueError("Invalid DER length")
    if sig[0] != 0x30:
        raise ValueError("Invalid DER sequence tag")
    total_len = sig[1]
    if total_len != len(sig) - 2:
        raise ValueError("Invalid DER sequence length")

    pos = 2
    if pos >= len(sig) or sig[pos] != 0x02:
        raise ValueError("Missing DER integer r tag")
    pos += 1
    if pos >= len(sig):
        raise ValueError("Missing DER r length")
    r_len = sig[pos]
    pos += 1
    if r_len == 0 or pos + r_len > len(sig):
        raise ValueError("Invalid DER r length")
    r_bytes = sig[pos:pos + r_len]
    pos += r_len

    if pos >= len(sig) or sig[pos] != 0x02:
        raise ValueError("Missing DER integer s tag")
    pos += 1
    if pos >= len(sig):
        raise ValueError("Missing DER s length")
    s_len = sig[pos]
    pos += 1
    if s_len == 0 or pos + s_len != len(sig):
        raise ValueError("Invalid DER s length")
    s_bytes = sig[pos:pos + s_len]

    # Canonical integer rules: positive, minimally-encoded.
    if r_bytes[0] & 0x80:
        raise ValueError("DER r must be positive")
    if s_bytes[0] & 0x80:
        raise ValueError("DER s must be positive")
    if len(r_bytes) > 1 and r_bytes[0] == 0x00 and not (r_bytes[1] & 0x80):
        raise ValueError("DER r has non-minimal encoding")
    if len(s_bytes) > 1 and s_bytes[0] == 0x00 and not (s_bytes[1] & 0x80):
        raise ValueError("DER s has non-minimal encoding")

    r = int.from_bytes(r_bytes, "big")
    s = int.from_bytes(s_bytes, "big")
    if r <= 0 or r >= CURVE_ORDER or s <= 0 or s >= CURVE_ORDER:
        raise ValueError("DER signature scalar out of range")
    return r, s


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
    try:
        r, s = _parse_der_signature_strict(signature)
    except Exception:
        return False
    return public_key.verify(message_hash, (r, s))


def sign_schnorr_message_hash(private_key: PrivateKey, message_hash: bytes) -> bytes:
    """Sign a 32-byte hash with BIP340 Schnorr."""
    return schnorr_sign_message(private_key.to_int(), message_hash)


def verify_schnorr_signature(pubkey_xonly: bytes, message_hash: bytes, signature: bytes) -> bool:
    """Verify BIP340 Schnorr signature over a 32-byte hash."""
    return schnorr_verify_message(pubkey_xonly, message_hash, signature)
