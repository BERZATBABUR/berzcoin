"""Minimal BIP32 xpub parsing and non-hardened child derivation helpers."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from typing import Tuple

from .base58 import base58_check_decode
from .keys import PublicKey
from .secp256k1 import GENERATOR, N as SECP256K1_ORDER, Point

XPUB_MAINNET = 0x0488B21E
XPUB_TESTNET = 0x043587CF


@dataclass(frozen=True)
class XPubData:
    version: int
    depth: int
    parent_fingerprint: bytes
    child_num: int
    chain_code: bytes
    public_key_bytes: bytes

    @property
    def network(self) -> str:
        if self.version == XPUB_MAINNET:
            return "mainnet"
        if self.version == XPUB_TESTNET:
            return "testnet"
        raise ValueError("unsupported xpub version")


def parse_xpub(xpub: str) -> XPubData:
    payload = base58_check_decode(str(xpub or "").strip())
    if len(payload) != 78:
        raise ValueError("invalid xpub payload length")
    version = int.from_bytes(payload[0:4], "big")
    depth = payload[4]
    parent_fp = payload[5:9]
    child_num = int.from_bytes(payload[9:13], "big")
    chain_code = payload[13:45]
    pubkey = payload[45:78]
    if pubkey[0] not in (0x02, 0x03):
        raise ValueError("xpub must contain compressed public key")
    # Validate pubkey is on curve.
    PublicKey.from_bytes(pubkey)
    return XPubData(
        version=version,
        depth=depth,
        parent_fingerprint=parent_fp,
        child_num=child_num,
        chain_code=chain_code,
        public_key_bytes=pubkey,
    )


def _hmac_sha512(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha512).digest()


def _fingerprint(pubkey_bytes: bytes) -> bytes:
    h1 = hashlib.sha256(pubkey_bytes).digest()
    h2 = hashlib.new("ripemd160", h1).digest()
    return h2[:4]


def _compress_point(p: Point) -> bytes:
    prefix = 0x02 if (p.y % 2 == 0) else 0x03
    return bytes([prefix]) + int(p.x).to_bytes(32, "big")


def derive_child_pubkey(parent_pubkey_bytes: bytes, parent_chain_code: bytes, index: int) -> Tuple[bytes, bytes]:
    """Derive non-hardened child pubkey and chaincode from parent xpub node."""
    if index < 0 or index >= 2**31:
        raise ValueError("xpub cannot derive hardened children")
    if len(parent_chain_code) != 32:
        raise ValueError("invalid chain code length")

    data = parent_pubkey_bytes + struct.pack(">I", int(index))
    i = _hmac_sha512(parent_chain_code, data)
    il, ir = i[:32], i[32:]
    tweak = int.from_bytes(il, "big")
    if tweak <= 0 or tweak >= SECP256K1_ORDER:
        raise ValueError("invalid child tweak")

    parent_pk = PublicKey.from_bytes(parent_pubkey_bytes)
    parent_point = Point(parent_pk.x, parent_pk.y)
    child_point = GENERATOR.multiply(tweak) + parent_point
    if child_point.inf:
        raise ValueError("invalid derived child point")
    return _compress_point(child_point), ir


def derive_xpub_external_pubkey(xpub: str, external_index: int) -> bytes:
    """Derive m/.../0/external_index pubkey from an account-level xpub."""
    node = parse_xpub(xpub)
    branch_pub, branch_cc = derive_child_pubkey(node.public_key_bytes, node.chain_code, 0)
    child_pub, _ = derive_child_pubkey(branch_pub, branch_cc, int(external_index))
    return child_pub

