"""Unit tests for crypto components."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from shared.core.hashes import hash256
from shared.crypto.address import public_key_to_address
from shared.crypto.base58 import (
    base58_check_decode,
    base58_check_encode,
    base58_decode,
    base58_encode,
)
from shared.crypto.bech32 import bech32_decode, bech32_encode
from shared.crypto.keys import PrivateKey, PublicKey
from shared.crypto.signatures import sign_message_hash, verify_signature

CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


class TestCrypto(unittest.TestCase):
    """Test crypto functionality."""

    def test_private_key_generation(self) -> None:
        key = PrivateKey()
        self.assertIsNotNone(key.key)
        self.assertIsInstance(key.key, int)
        self.assertTrue(0 < key.key < CURVE_ORDER)

    def test_public_key_derivation(self) -> None:
        private_key = PrivateKey(12345)
        public_key = private_key.public_key()
        self.assertIsNotNone(public_key.x)
        self.assertIsNotNone(public_key.y)
        self.assertIsInstance(public_key.x, int)
        self.assertIsInstance(public_key.y, int)

    def test_public_key_serialization(self) -> None:
        private_key = PrivateKey(12345)
        public_key = private_key.public_key()
        compressed = public_key.to_bytes(compressed=True)
        self.assertEqual(len(compressed), 33)
        uncompressed = public_key.to_bytes(compressed=False)
        self.assertEqual(len(uncompressed), 65)
        recovered = PublicKey.from_bytes(compressed)
        self.assertEqual(recovered.x, public_key.x)
        self.assertEqual(recovered.y, public_key.y)

    def test_signature_verification(self) -> None:
        private_key = PrivateKey(12345)
        public_key = private_key.public_key()
        message_hash = hash256(b"Hello, Bitcoin!")
        signature = sign_message_hash(private_key, message_hash)
        self.assertIsNotNone(signature)
        self.assertTrue(len(signature) > 0)
        self.assertTrue(verify_signature(public_key, message_hash, signature))

    def test_address_generation(self) -> None:
        private_key = PrivateKey(12345)
        public_key = private_key.public_key()
        address = public_key_to_address(public_key, network="mainnet", segwit=False)
        self.assertTrue(address.startswith("1"))
        self.assertGreaterEqual(len(address), 26)
        segwit_address = public_key_to_address(public_key, network="mainnet", segwit=True)
        self.assertTrue(segwit_address.startswith("bc1"))

    def test_base58_encoding(self) -> None:
        data = b"Hello Bitcoin"
        encoded = base58_encode(data)
        decoded = base58_decode(encoded)
        self.assertEqual(data, decoded)

    def test_base58_check(self) -> None:
        payload = b"\x00" + b"\x01" * 20
        encoded = base58_check_encode(payload)
        decoded = base58_check_decode(encoded)
        self.assertEqual(payload, decoded)
        with self.assertRaises(ValueError):
            base58_check_decode(encoded[:-1] + "z")

    def test_bech32_encoding(self) -> None:
        hrp = "bc"
        witver = 0
        witprog = b"\x01" * 20
        encoded = bech32_encode(hrp, witver, witprog)
        decoded_hrp, decoded_ver, decoded_prog = bech32_decode(encoded)
        self.assertEqual(decoded_hrp, hrp)
        self.assertEqual(decoded_ver, witver)
        self.assertEqual(decoded_prog, witprog)


if __name__ == "__main__":
    unittest.main()
