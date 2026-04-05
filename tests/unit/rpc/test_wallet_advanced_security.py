"""Tests for optional advanced wallet security surfaces (watch-only/xpub, PSBT, multisig)."""

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.wallet import WalletHandlers
from shared.crypto.address import hash160, public_key_to_address
from shared.crypto.base58 import base58_check_encode
from shared.crypto.hd import bip32_ckd_priv, bip32_master_key_from_seed, mnemonic_to_seed
from shared.crypto.keys import PrivateKey
from node.wallet.core.tx_builder import TransactionBuilder


def _make_account_tpub() -> str:
    """Build a deterministic account-level tpub at m/44'/1'/0'."""
    mnemonic = (
        "abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon about"
    )
    seed = mnemonic_to_seed(mnemonic, "")
    k, c = bip32_master_key_from_seed(seed)
    k44, c44 = bip32_ckd_priv(k, c, 44 | 0x80000000)
    kcoin, ccoin = bip32_ckd_priv(k44, c44, 1 | 0x80000000)
    kacct, cacct = bip32_ckd_priv(kcoin, ccoin, 0 | 0x80000000)

    depth = bytes([3])
    parent_fp = hash160(PrivateKey(kcoin).public_key().to_bytes(compressed=True))[:4]
    child_num = (0x80000000).to_bytes(4, "big")
    chain_code = cacct
    pub = PrivateKey(kacct).public_key().to_bytes(compressed=True)
    version_tpub = bytes.fromhex("043587CF")
    payload = version_tpub + depth + parent_fp + child_num + chain_code + pub
    return base58_check_encode(payload)


class _Config:
    def __init__(self, datadir: Path):
        self._datadir = datadir
        self._values = {
            "network": "regtest",
            "wallet_debug_secrets": False,
            "debug": False,
            "wallet_encryption_passphrase": "unit-test-passphrase",
            "wallet_default_unlock_timeout": 300,
        }

    def get_datadir(self) -> Path:
        return self._datadir

    def get(self, key, default=None):
        return self._values.get(key, default)


class _ChainState:
    def __init__(self):
        self.sender_address = ""
        self._sender_spk = b""

    def set_sender(self, sender_address: str) -> None:
        self.sender_address = sender_address
        self._sender_spk = TransactionBuilder("regtest")._create_script_pubkey(sender_address)

    def get_balance(self, _address: str) -> int:
        return 0

    def get_best_height(self) -> int:
        return 200

    def get_utxos_for_address(self, address: str, _limit: int = 1000):
        if address != self.sender_address:
            return []
        return [
            {
                "txid": "55" * 32,
                "index": 0,
                "value": 250_000,
                "script_pubkey": self._sender_spk,
                "height": 100,
                "is_coinbase": False,
            }
        ]

    def get_utxo(self, txid: str, index: int):
        if txid == ("55" * 32) and int(index) == 0:
            return {
                "txid": txid,
                "index": 0,
                "value": 250_000,
                "script_pubkey": self._sender_spk,
                "height": 100,
                "is_coinbase": False,
            }
        return None


class _Mempool:
    def __init__(self):
        self.policy = type("Policy", (), {"min_relay_fee": 1, "dust_threshold": 546})()

    async def add_transaction(self, tx):
        _ = tx
        return True


class _Node:
    def __init__(self, datadir: Path):
        self.config = _Config(datadir)
        self.chainstate = _ChainState()
        self.simple_wallet_manager = None
        self.mempool = _Mempool()


class TestWalletAdvancedSecurity(unittest.TestCase):
    def test_watch_only_xpub_import_and_derive(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)
                tpub = _make_account_tpub()

                imported = await handlers.import_xpub_watchonly(tpub, "watch")
                self.assertTrue(imported.get("watch_only"))
                info1 = await handlers.get_wallet_info()
                self.assertTrue(info1.get("watch_only"))
                self.assertFalse(info1.get("private_keys_enabled"))
                addr1 = info1.get("address")
                addr2 = await handlers.get_new_address()
                self.assertNotEqual(addr1, addr2)

                recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")
                with self.assertRaises(ValueError):
                    await handlers.send_to_address(recipient, 0.001)

        asyncio.run(run())

    def test_psbt_create_process_finalize(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)

                created = await handlers.create_wallet()
                node.chainstate.set_sender(created["address"])
                recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")

                psbt_obj = await handlers.wallet_create_funded_psbt(recipient, 0.001)
                self.assertIn("psbt", psbt_obj)
                raw = json.loads(base64.b64decode(psbt_obj["psbt"]).decode("utf-8"))
                self.assertEqual(raw.get("format"), "berzcoin.psbt.v1")

                processed = await handlers.wallet_process_psbt(psbt_obj["psbt"], True)
                self.assertTrue(processed.get("complete"))

                finalized = await handlers.finalize_psbt(processed["psbt"])
                self.assertTrue(finalized.get("complete"))
                self.assertTrue(bool(finalized.get("hex")))

        asyncio.run(run())

    def test_create_multisig_policy_persists(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                node = _Node(Path(tmp))
                handlers = WalletHandlers(node)
                p1 = PrivateKey().public_key().to_bytes(compressed=True).hex()
                p2 = PrivateKey().public_key().to_bytes(compressed=True).hex()

                policy = await handlers.create_multisig_policy(2, [p1, p2], "ops")
                self.assertEqual(policy.get("required"), 2)
                self.assertTrue(bool(policy.get("address")))
                self.assertTrue(bool(policy.get("redeem_script")))

                policies_file = Path(tmp) / "wallets" / "multisig_policies.json"
                self.assertTrue(policies_file.exists())
                loaded = json.loads(policies_file.read_text(encoding="utf-8"))
                self.assertTrue(any(p.get("policy_id") == policy.get("policy_id") for p in loaded))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

