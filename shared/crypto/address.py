"""Bitcoin address generation."""

import hashlib
from typing import Optional
from .keys import PublicKey
from .base58 import base58_check_encode
from .bech32 import bech32_encode

def hash160(data: bytes) -> bytes:
    """Calculate RIPEMD160(SHA256(data)).

    Args:
        data: Input data

    Returns:
        20-byte hash160
    """
    sha256_hash = hashlib.sha256(data).digest()
    ripemd160_hash = hashlib.new('ripemd160', sha256_hash).digest()
    return ripemd160_hash

def public_key_to_address(
    public_key: PublicKey,
    network: str = "mainnet",
    compressed: bool = True,
    segwit: bool = False
) -> str:
    """Convert public key to Bitcoin address.

    Args:
        public_key: Public key
        network: Network (mainnet, testnet, regtest)
        compressed: Use compressed public key
        segwit: Generate SegWit address (bech32)

    Returns:
        Address string
    """
    # Network prefixes
    prefixes = {
        "mainnet": {"p2pkh": b"\x00", "p2sh": b"\x05", "hrp": "bc"},
        "testnet": {"p2pkh": b"\x6f", "p2sh": b"\xc4", "hrp": "tb"},
        "regtest": {"p2pkh": b"\x6f", "p2sh": b"\xc4", "hrp": "bcrt"},
    }

    prefix = prefixes[network]

    # Serialize public key
    pubkey_bytes = public_key.to_bytes(compressed)

    if segwit:
        # SegWit address (bech32)
        witness_program = hash160(pubkey_bytes)
        return bech32_encode(prefix['hrp'], 0, witness_program)
    else:
        # Legacy address (P2PKH)
        pubkey_hash = hash160(pubkey_bytes)
        return base58_check_encode(prefix['p2pkh'] + pubkey_hash)

def script_to_address(
    script_hash: bytes,
    network: str = "mainnet"
) -> str:
    """Convert script hash to P2SH address.

    Args:
        script_hash: 20-byte script hash
        network: Network (mainnet, testnet, regtest)

    Returns:
        P2SH address
    """
    prefixes = {
        "mainnet": b"\x05",
        "testnet": b"\xc4",
        "regtest": b"\xc4",
    }

    return base58_check_encode(prefixes[network] + script_hash)
