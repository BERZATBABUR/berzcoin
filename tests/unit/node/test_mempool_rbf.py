"""Unit tests for basic RBF package policy behavior."""

import asyncio
import unittest

from node.mempool.pool import Mempool
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import calculate_legacy_sighash, SIGHASH_ALL


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


def _signed_spend(key: PrivateKey, prev_txid_hex: str, value: int, spend_value: int, sequence: int) -> Transaction:
    pub = key.public_key().to_bytes()
    pkh = hash160(pub)
    spk = _p2pkh_script(pkh)

    tx = Transaction(version=2)
    tx.vin = [
        TxIn(
            prev_tx_hash=bytes.fromhex(prev_txid_hex),
            prev_tx_index=0,
            sequence=sequence,
        )
    ]
    recipient = PrivateKey().public_key().to_bytes()
    tx.vout = [TxOut(spend_value, _p2pkh_script(hash160(recipient)))]

    sighash = calculate_legacy_sighash(tx, 0, SIGHASH_ALL, spk)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[0].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub

    _ = value
    return tx


class TestMempoolRBF(unittest.TestCase):
    def test_opt_in_rbf_replacement(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            prev_txid = "55" * 32
            utxo = {
                "value": 100_000,
                "script_pubkey": _p2pkh_script(hash160(pub)),
            }
            chainstate = _ChainStateStub({(prev_txid, 0): utxo}, {prev_txid})
            mempool = Mempool(chainstate)

            old_tx = _signed_spend(key, prev_txid, 100_000, 90_000, sequence=0xFFFFFFFD)
            new_tx = _signed_spend(key, prev_txid, 100_000, 85_000, sequence=0xFFFFFFFD)

            self.assertTrue(await mempool.add_transaction(old_tx))
            self.assertTrue(await mempool.add_transaction(new_tx))
            self.assertNotIn(old_tx.txid().hex(), mempool.transactions)
            self.assertIn(new_tx.txid().hex(), mempool.transactions)

        asyncio.run(run())

    def test_non_opt_in_conflict_rejected(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            prev_txid = "66" * 32
            utxo = {
                "value": 100_000,
                "script_pubkey": _p2pkh_script(hash160(pub)),
            }
            chainstate = _ChainStateStub({(prev_txid, 0): utxo}, {prev_txid})
            mempool = Mempool(chainstate)

            old_tx = _signed_spend(key, prev_txid, 100_000, 90_000, sequence=0xFFFFFFFF)
            new_tx = _signed_spend(key, prev_txid, 100_000, 85_000, sequence=0xFFFFFFFD)

            self.assertTrue(await mempool.add_transaction(old_tx))
            self.assertFalse(await mempool.add_transaction(new_tx))
            self.assertEqual(mempool.last_reject_reason, "rbf_policy")

        asyncio.run(run())

    def test_inherited_rbf_from_unconfirmed_ancestor(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient_key = PrivateKey()
            replacement_recipient = PrivateKey()

            funding_pub = funding_key.public_key().to_bytes()
            child_pub = child_key.public_key().to_bytes()
            prev_txid = "77" * 32

            chainstate = _ChainStateStub(
                {
                    (prev_txid, 0): {
                        "value": 120_000,
                        "script_pubkey": _p2pkh_script(hash160(funding_pub)),
                    }
                },
                {prev_txid},
            )
            mempool = Mempool(chainstate)

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(100_000, _p2pkh_script(hash160(child_pub)))]
            sighash_parent = calculate_legacy_sighash(parent, 0, SIGHASH_ALL, _p2pkh_script(hash160(funding_pub)))
            sig_parent = sign_message_hash(funding_key, sighash_parent) + bytes([SIGHASH_ALL])
            parent.vin[0].script_sig = bytes([len(sig_parent)]) + sig_parent + bytes([len(funding_pub)]) + funding_pub

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFF)]
            child.vout = [TxOut(90_000, _p2pkh_script(hash160(recipient_key.public_key().to_bytes())))]
            sighash_child = calculate_legacy_sighash(child, 0, SIGHASH_ALL, _p2pkh_script(hash160(child_pub)))
            sig_child = sign_message_hash(child_key, sighash_child) + bytes([SIGHASH_ALL])
            child.vin[0].script_sig = bytes([len(sig_child)]) + sig_child + bytes([len(child_pub)]) + child_pub

            replacement = Transaction(version=2)
            replacement.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFF)]
            replacement.vout = [TxOut(80_000, _p2pkh_script(hash160(replacement_recipient.public_key().to_bytes())))]
            sighash_repl = calculate_legacy_sighash(replacement, 0, SIGHASH_ALL, _p2pkh_script(hash160(child_pub)))
            sig_repl = sign_message_hash(child_key, sighash_repl) + bytes([SIGHASH_ALL])
            replacement.vin[0].script_sig = bytes([len(sig_repl)]) + sig_repl + bytes([len(child_pub)]) + child_pub

            self.assertTrue(await mempool.add_transaction(parent))
            self.assertTrue(await mempool.add_transaction(child))
            self.assertTrue(await mempool.add_transaction(replacement))
            self.assertNotIn(child.txid().hex(), mempool.transactions)
            self.assertIn(replacement.txid().hex(), mempool.transactions)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
