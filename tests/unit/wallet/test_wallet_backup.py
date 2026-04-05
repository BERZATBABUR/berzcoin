"""Simple-wallet backup/recovery regression tests."""

import base64
import json
import os
import shutil
import tempfile
import unittest

from node.wallet.simple_wallet import SimpleWallet
from node.wallet.storage.backup import WalletBackup


class TestWalletBackup(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.wallet_path = os.path.join(self.temp_dir, "wallet.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_backup_restore_roundtrip(self) -> None:
        wallet = SimpleWallet.create(network="regtest")
        with open(self.wallet_path, "w", encoding="utf-8") as f:
            json.dump(wallet.to_dict(), f)

        backup = WalletBackup(self.wallet_path)
        backup_path = backup.create_backup("roundtrip", network="regtest")
        self.assertTrue(backup_path)

        # Corrupt wallet file and ensure restore brings it back.
        with open(self.wallet_path, "wb") as f:
            f.write(b"corrupt")

        self.assertTrue(backup.restore_backup("roundtrip", expected_network="regtest"))
        with open(self.wallet_path, "r", encoding="utf-8") as f:
            restored = json.load(f)
        self.assertEqual(restored["private_key"], wallet.private_key_hex)
        self.assertEqual(restored["address"], wallet.address)

    def test_encrypted_backup_wrong_password_rejected(self) -> None:
        wallet = SimpleWallet.create(network="regtest")
        with open(self.wallet_path, "w", encoding="utf-8") as f:
            json.dump(wallet.to_dict(), f)

        backup = WalletBackup(self.wallet_path)
        created = backup.create_encrypted_backup("enc_wrong_pw", "correct-password", network="regtest")
        self.assertTrue(created)
        self.assertFalse(
            backup.restore_encrypted_backup(
                "enc_wrong_pw",
                "wrong-password",
                expected_network="regtest",
            )
        )

    def test_encrypted_backup_tamper_detected(self) -> None:
        wallet = SimpleWallet.create(network="regtest")
        with open(self.wallet_path, "w", encoding="utf-8") as f:
            json.dump(wallet.to_dict(), f)

        backup = WalletBackup(self.wallet_path)
        created = backup.create_encrypted_backup("enc_tamper", "secret", network="regtest")
        self.assertTrue(created)
        enc_path = os.path.join(self.temp_dir, "backups", "enc_tamper.encrypted")
        with open(enc_path, "r", encoding="utf-8") as f:
            envelope = json.load(f)

        c = bytearray(base64.b64decode(envelope["ciphertext_b64"]))
        c[0] ^= 0x01
        envelope["ciphertext_b64"] = base64.b64encode(bytes(c)).decode("ascii")
        with open(enc_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2)

        self.assertFalse(
            backup.restore_encrypted_backup(
                "enc_tamper",
                "secret",
                expected_network="regtest",
            )
        )

    def test_encrypted_backup_network_mismatch_rejected(self) -> None:
        wallet = SimpleWallet.create(network="regtest")
        with open(self.wallet_path, "w", encoding="utf-8") as f:
            json.dump(wallet.to_dict(), f)

        backup = WalletBackup(self.wallet_path)
        created = backup.create_encrypted_backup("enc_network_guard", "secret", network="regtest")
        self.assertTrue(created)
        self.assertFalse(
            backup.restore_encrypted_backup(
                "enc_network_guard",
                "secret",
                expected_network="mainnet",
            )
        )


if __name__ == "__main__":
    unittest.main()
