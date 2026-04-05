"""BIP39/BIP32/BIP44 helpers for deterministic wallet derivation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
import unicodedata
from pathlib import Path
from typing import List, Tuple

from .keys import PrivateKey
from .secp256k1 import N as SECP256K1_ORDER


def _wordlist_path() -> Path:
    return Path(__file__).with_name("bip39_english.txt")


def load_bip39_english_wordlist() -> List[str]:
    words = _wordlist_path().read_text(encoding="utf-8").strip().splitlines()
    if len(words) != 2048:
        raise ValueError("BIP39 English wordlist must contain 2048 words")
    return words


def generate_bip39_mnemonic(strength_bits: int = 128) -> str:
    if strength_bits not in (128, 160, 192, 224, 256):
        raise ValueError("Invalid BIP39 entropy strength")
    entropy = secrets.token_bytes(strength_bits // 8)
    checksum_len = strength_bits // 32
    checksum = hashlib.sha256(entropy).digest()
    entropy_bits = "".join(f"{b:08b}" for b in entropy)
    checksum_bits = "".join(f"{b:08b}" for b in checksum)[:checksum_len]
    bits = entropy_bits + checksum_bits
    words = load_bip39_english_wordlist()
    out = []
    for i in range(0, len(bits), 11):
        idx = int(bits[i:i + 11], 2)
        out.append(words[idx])
    return " ".join(out)


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    m = unicodedata.normalize("NFKD", str(mnemonic or ""))
    p = unicodedata.normalize("NFKD", str(passphrase or ""))
    salt = f"mnemonic{p}".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha512", m.encode("utf-8"), salt, 2048, dklen=64)


def _hmac_sha512(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha512).digest()


def bip32_master_key_from_seed(seed: bytes) -> Tuple[int, bytes]:
    i = _hmac_sha512(b"Bitcoin seed", seed)
    il, ir = i[:32], i[32:]
    k = int.from_bytes(il, "big")
    if k <= 0 or k >= SECP256K1_ORDER:
        raise ValueError("Invalid master key from seed")
    return k, ir


def bip32_ckd_priv(parent_key: int, parent_chain_code: bytes, index: int) -> Tuple[int, bytes]:
    if index < 0 or index >= 2**32:
        raise ValueError("Invalid child index")
    if len(parent_chain_code) != 32:
        raise ValueError("Invalid parent chain code length")
    if parent_key <= 0 or parent_key >= SECP256K1_ORDER:
        raise ValueError("Invalid parent private key")

    hardened = index >= 0x80000000
    if hardened:
        data = b"\x00" + parent_key.to_bytes(32, "big") + struct.pack(">I", index)
    else:
        pub = PrivateKey(parent_key).public_key().to_bytes(compressed=True)
        data = pub + struct.pack(">I", index)

    i = _hmac_sha512(parent_chain_code, data)
    il, ir = i[:32], i[32:]
    child = (int.from_bytes(il, "big") + parent_key) % SECP256K1_ORDER
    if child == 0:
        raise ValueError("Derived invalid child key")
    return child, ir


def derive_bip44_private_key(
    seed: bytes,
    coin_type: int,
    account: int,
    change: int,
    address_index: int,
) -> Tuple[int, str]:
    """Derive BIP44 private key at m/44'/coin_type'/account'/change/address_index."""
    if coin_type < 0 or account < 0 or change not in (0, 1) or address_index < 0:
        raise ValueError("Invalid BIP44 path elements")

    k, c = bip32_master_key_from_seed(seed)
    path_items = [
        44 | 0x80000000,
        int(coin_type) | 0x80000000,
        int(account) | 0x80000000,
        int(change),
        int(address_index),
    ]
    for item in path_items:
        k, c = bip32_ckd_priv(k, c, item)

    path = f"m/44'/{coin_type}'/{account}'/{change}/{address_index}"
    return k, path

