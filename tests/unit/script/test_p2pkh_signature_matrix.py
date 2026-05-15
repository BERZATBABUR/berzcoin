"""Dedicated P2PKH script/signature matrix and boundary checks."""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from node.chain.validation import BlockValidator
from node.rpc.handlers.wallet import WalletHandlers
from shared.consensus.params import ConsensusParams
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.address import public_key_to_address
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.crypto.secp256k1 import N as SECP256K1_ORDER
from shared.script.opcodes import Opcode
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
from shared.script.verify import verify_input_script


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return (
        bytes([Opcode.OP_DUP, Opcode.OP_HASH160, 0x14])
        + pubkey_hash
        + bytes([Opcode.OP_EQUALVERIFY, Opcode.OP_CHECKSIG])
    )


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes, sighash_type: int = SIGHASH_ALL) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, sighash_type, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([sighash_type & 0xFF])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


def _encode_der(r: int, s: int) -> bytes:
    def _enc(n: int) -> bytes:
        b = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
        if b[0] & 0x80:
            b = b"\x00" + b
        return b

    rb = _enc(r)
    sb = _enc(s)
    return b"\x30" + bytes([len(rb) + len(sb) + 4]) + b"\x02" + bytes([len(rb)]) + rb + b"\x02" + bytes([len(sb)]) + sb


def _der_to_rs(signature_der: bytes) -> tuple[int, int]:
    r_len = signature_der[3]
    r = int.from_bytes(signature_der[4:4 + r_len], "big")
    s_len_pos = 4 + r_len + 1
    s_len = signature_der[s_len_pos]
    s_start = s_len_pos + 1
    s = int.from_bytes(signature_der[s_start:s_start + s_len], "big")
    return r, s


class _UTXOStore:
    def __init__(self, utxos):
        self._utxos = dict(utxos)

    def get_utxo(self, txid, index):
        return self._utxos.get((txid, int(index)))


class _BlockIndex:
    def __init__(self, prev_hash: str):
        self.prev_hash = prev_hash

    def get_block(self, block_hash):
        return object() if block_hash == self.prev_hash else None


class _Cfg:
    def __init__(self, datadir: Path):
        self._datadir = datadir
        self._v = {
            "network": "regtest",
            "wallet_encryption_passphrase": "unit-test-passphrase",
            "wallet_default_unlock_timeout": 300,
            "wallet_debug_secrets": False,
            "debug": False,
        }

    def get_datadir(self):
        return self._datadir

    def get(self, key, default=None):
        return self._v.get(key, default)


class _Mempool:
    def __init__(self):
        self.policy = SimpleNamespace(min_relay_fee=1, dust_threshold=546)
        self.last_tx = None

    async def add_transaction(self, tx):
        self.last_tx = tx
        return True


class _ChainStateWalletStub:
    def __init__(self, utxo):
        self._utxo = utxo
        self.params = SimpleNamespace(coinbase_maturity=100)

    def get_best_height(self):
        return 200

    def get_utxos_for_address(self, _address: str, _limit: int = 1000):
        return [self._utxo]

    def get_utxo(self, txid: str, index: int):
        if txid == self._utxo["txid"] and int(index) == int(self._utxo["index"]):
            return self._utxo
        return None

    def get_balance(self, _address: str):
        return int(self._utxo["value"])

    def transaction_exists(self, _txid: str):
        return False


class _Node:
    def __init__(self, datadir: Path, chainstate):
        self.config = _Cfg(datadir)
        self.chainstate = chainstate
        self.mempool = _Mempool()
        self.simple_wallet_manager = None


class TestP2PKHSignatureMatrix(unittest.TestCase):
    def test_valid_p2pkh_spend(self):
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("11" * 32), prev_tx_index=0, sequence=0xFFFFFFFD)]
        tx.vout = [TxOut(90_000, spk)]
        _sign_input(tx, 0, key, spk, SIGHASH_ALL)
        self.assertTrue(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_wrong_signature_rejected(self):
        key = PrivateKey()
        wrong = PrivateKey()
        pub = key.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("12" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, spk)]
        _sign_input(tx, 0, wrong, spk, SIGHASH_ALL)
        # Keep correct pubkey with wrong signature.
        sig = tx.vin[0].script_sig[1 : 1 + tx.vin[0].script_sig[0]]
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_wrong_public_key_rejected(self):
        key = PrivateKey()
        wrong = PrivateKey()
        pub = key.public_key().to_bytes()
        wrong_pub = wrong.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("13" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, spk)]
        _sign_input(tx, 0, key, spk, SIGHASH_ALL)
        sig = tx.vin[0].script_sig[1 : 1 + tx.vin[0].script_sig[0]]
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(wrong_pub)]) + wrong_pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_wrong_pubkey_hash_rejected(self):
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        wrong_hash = hash160(PrivateKey().public_key().to_bytes())
        spk = _p2pkh_script(wrong_hash)
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("14" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, b"\x51")]
        _sign_input(tx, 0, key, spk, SIGHASH_ALL)
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_malformed_der_signature_rejected(self):
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("15" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, b"\x51")]
        bad_sig = b"\x01\x02\x03\x01"
        tx.vin[0].script_sig = bytes([len(bad_sig)]) + bad_sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_high_s_signature_rejected(self):
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("16" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, b"\x51")]
        sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, spk)
        der = sign_message_hash(key, sighash)
        r, s = _der_to_rs(der)
        high_s = SECP256K1_ORDER - s
        bad_der = _encode_der(r, high_s)
        sig = bad_der + bytes([SIGHASH_ALL])
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_unsupported_sighash_type_rejected(self):
        key = PrivateKey()
        pub = key.public_key().to_bytes()
        spk = _p2pkh_script(hash160(pub))
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex("17" * 32), prev_tx_index=0)]
        tx.vout = [TxOut(90_000, b"\x51")]
        sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, spk)
        der = sign_message_hash(key, sighash)
        bad_flag = 0x04
        sig = der + bytes([bad_flag])
        tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub
        self.assertFalse(verify_input_script(tx, 0, tx.vin[0].script_sig, spk, 100_000))

    def test_multi_input_all_valid(self):
        key1 = PrivateKey()
        key2 = PrivateKey()
        spk1 = _p2pkh_script(hash160(key1.public_key().to_bytes()))
        spk2 = _p2pkh_script(hash160(key2.public_key().to_bytes()))
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=bytes.fromhex("21" * 32), prev_tx_index=0),
            TxIn(prev_tx_hash=bytes.fromhex("22" * 32), prev_tx_index=1),
        ]
        tx.vout = [TxOut(150_000, b"\x51")]
        _sign_input(tx, 0, key1, spk1, SIGHASH_ALL)
        _sign_input(tx, 1, key2, spk2, SIGHASH_ALL)
        self.assertTrue(verify_input_script(tx, 0, tx.vin[0].script_sig, spk1, 100_000))
        self.assertTrue(verify_input_script(tx, 1, tx.vin[1].script_sig, spk2, 100_000))

    def test_multi_input_one_invalid(self):
        key1 = PrivateKey()
        key2 = PrivateKey()
        wrong = PrivateKey()
        spk1 = _p2pkh_script(hash160(key1.public_key().to_bytes()))
        spk2 = _p2pkh_script(hash160(key2.public_key().to_bytes()))
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=bytes.fromhex("23" * 32), prev_tx_index=0),
            TxIn(prev_tx_hash=bytes.fromhex("24" * 32), prev_tx_index=1),
        ]
        tx.vout = [TxOut(150_000, b"\x51")]
        _sign_input(tx, 0, key1, spk1, SIGHASH_ALL)
        _sign_input(tx, 1, wrong, spk2, SIGHASH_ALL)
        self.assertTrue(verify_input_script(tx, 0, tx.vin[0].script_sig, spk1, 100_000))
        self.assertFalse(verify_input_script(tx, 1, tx.vin[1].script_sig, spk2, 100_000))

    def test_wallet_created_signed_transaction_passes_validator(self):
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                sender_key = PrivateKey()
                sender_addr = public_key_to_address(sender_key.public_key(), network="regtest")
                sender_spk = _p2pkh_script(hash160(sender_key.public_key().to_bytes()))
                utxo = {
                    "txid": "31" * 32,
                    "index": 0,
                    "value": 250_000,
                    "script_pubkey": sender_spk,
                    "height": 100,
                    "is_coinbase": False,
                    "address": sender_addr,
                }

                node = _Node(Path(tmp), _ChainStateWalletStub(utxo))
                handlers = WalletHandlers(node)
                await handlers.load_wallet(sender_key.to_hex())
                recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")
                txid = await handlers.send_to_address(recipient, 0.001)
                self.assertTrue(bool(txid))
                signed_tx = node.mempool.last_tx
                self.assertIsNotNone(signed_tx)

                params = ConsensusParams.regtest()
                validator = BlockValidator(
                    params,
                    _UTXOStore({(utxo["txid"], 0): utxo}),
                    _BlockIndex("aa" * 32),
                )
                self.assertTrue(validator.validate_transaction(signed_tx, height=200, is_coinbase=False))

        asyncio.run(run())

    def test_script_engine_has_no_forbidden_runtime_imports(self):
        forbidden = ("node.wallet", "node.rpc", "node.p2p", "node.mining", "node.web")
        script_dir = Path("shared/script")
        for path in script_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, msg=f"Forbidden import surface in {path}: {token}")

    def test_validation_paths_use_shared_verify_input_script(self):
        from node.chain.validation import BlockValidator as _BV
        from node.mempool.pool import Mempool as _MP

        src_block = inspect.getsource(_BV.validate_transaction)
        src_mempool = inspect.getsource(_MP._validate_transaction)
        self.assertIn("verify_input_script(", src_block)
        self.assertIn("verify_input_script(", src_mempool)


if __name__ == "__main__":
    unittest.main()
