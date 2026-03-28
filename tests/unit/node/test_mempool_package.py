"""Unit tests for package relay behavior."""

import asyncio
import unittest

from node.mempool.pool import Mempool
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _ChainStateStub:
    def __init__(self, utxos, known_txids):
        self._utxos = utxos
        self._known = set(known_txids)

    def transaction_exists(self, txid: str) -> bool:
        return txid in self._known

    def get_utxo(self, txid: str, index: int):
        return self._utxos.get((txid, index))

    def get_best_height(self) -> int:
        return 100


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _build_signed_input(tx: Transaction, input_index: int, key: PrivateKey, prev_script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, input_index, SIGHASH_ALL, prev_script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[input_index].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


class TestMempoolPackage(unittest.TestCase):
    def test_add_parent_child_package(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient_key = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            recipient_spk = _p2pkh_script(hash160(recipient_key.public_key().to_bytes()))

            prev_txid = "aa" * 32
            chainstate = _ChainStateStub({(prev_txid, 0): {"value": 200_000, "script_pubkey": funding_spk}}, {prev_txid})
            mempool = Mempool(chainstate)

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(140_000, child_spk)]
            _build_signed_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(120_000, recipient_spk)]
            _build_signed_input(child, 0, child_key, child_spk)

            result = await mempool.add_package([child, parent])
            self.assertTrue(result.get("accepted"))
            self.assertIn(parent.txid().hex(), mempool.transactions)
            self.assertIn(child.txid().hex(), mempool.transactions)

        asyncio.run(run())

    def test_package_rollback_on_failure(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient_key = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            recipient_spk = _p2pkh_script(hash160(recipient_key.public_key().to_bytes()))

            prev_txid = "bb" * 32
            chainstate = _ChainStateStub({(prev_txid, 0): {"value": 210_000, "script_pubkey": funding_spk}}, {prev_txid})
            mempool = Mempool(chainstate)

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(150_000, child_spk)]
            _build_signed_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(140_000, recipient_spk)]
            # Intentionally malformed signature/scriptSig for failure.
            child.vin[0].script_sig = b"\x01\x01"

            result = await mempool.add_package([parent, child])
            self.assertFalse(result.get("accepted"))
            self.assertEqual(len(mempool.transactions), 0)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
