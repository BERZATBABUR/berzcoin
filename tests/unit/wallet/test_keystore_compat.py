"""Wallet compatibility tests (WIF/private-key imports)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from node.wallet.core.keystore import KeyStore
from shared.crypto.keys import PrivateKey
from shared.crypto.address import public_key_to_address


class TestKeyStoreCompat(unittest.TestCase):
    def test_import_wif_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ks = KeyStore(os.path.join(td, "wallet.dat"), network="regtest")
            priv = PrivateKey(123456789)
            wif = priv.to_wif(network="regtest", compressed=True)
            addr = ks.import_private_key(wif)
            self.assertIsNotNone(addr)
            expected = public_key_to_address(priv.public_key(), network="regtest")
            self.assertEqual(addr, expected)


if __name__ == "__main__":
    unittest.main()
