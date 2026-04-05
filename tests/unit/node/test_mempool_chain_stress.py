"""Stress tests for deep unconfirmed ancestor/descendant chains."""

import asyncio
import unittest

from node.mempool.limits import MempoolLimits
from node.mempool.pool import Mempool
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _ChainStateStub:
    def __init__(self, utxos, known_txids):
        self._utxos = dict(utxos)
        self._known = set(known_txids)
        self.params = type(
            "Params",
            (),
            {
                "coinbase_maturity": 100,
                "max_money": 21_000_000 * 100_000_000,
                "custom_activation_heights": {},
            },
        )()

    def transaction_exists(self, txid: str) -> bool:
        return txid in self._known

    def get_utxo(self, txid: str, index: int):
        return self._utxos.get((txid, index))

    def get_best_height(self) -> int:
        return 100


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


def _build_child(parent_txid_hex: str, value_in: int, value_out: int, key: PrivateKey, spk: bytes) -> Transaction:
    tx = Transaction(version=2)
    tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(parent_txid_hex), prev_tx_index=0, sequence=0xFFFFFFFD)]
    tx.vout = [TxOut(value_out, spk)]
    _ = value_in
    _sign_input(tx, 0, key, spk)
    return tx


class TestMempoolChainStress(unittest.TestCase):
    def test_deep_chain_hits_ancestor_count_boundary(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev = "ef" * 32
            cs = _ChainStateStub({(prev, 0): {"value": 1_000_000, "script_pubkey": spk}}, {prev})
            limits = MempoolLimits(max_ancestors=5)
            mempool = Mempool(cs, limits=limits)

            current_parent = prev
            current_value = 1_000_000
            accepted = 0
            # First 6 txs should pass: ancestor counts 0..5.
            for _ in range(6):
                child = _build_child(current_parent, current_value, current_value - 1_000, key, spk)
                self.assertTrue(await mempool.add_transaction(child))
                accepted += 1
                current_parent = child.txid().hex()
                current_value -= 1_000

            # Next child has 6 ancestors and should be rejected.
            overflow = _build_child(current_parent, current_value, current_value - 1_000, key, spk)
            self.assertFalse(await mempool.add_transaction(overflow))
            self.assertEqual(mempool.last_reject_reason, "too_many_ancestors")
            self.assertEqual(len(mempool.transactions), accepted)

        asyncio.run(run())

    def test_deep_chain_hits_descendant_count_boundary(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev = "ab" * 32
            cs = _ChainStateStub({(prev, 0): {"value": 1_000_000, "script_pubkey": spk}}, {prev})
            limits = MempoolLimits(max_descendants=3)
            mempool = Mempool(cs, limits=limits)

            parent = _build_child(prev, 1_000_000, 999_000, key, spk)
            self.assertTrue(await mempool.add_transaction(parent))

            c1 = _build_child(parent.txid().hex(), 999_000, 998_000, key, spk)
            self.assertTrue(await mempool.add_transaction(c1))
            c2 = _build_child(c1.txid().hex(), 998_000, 997_000, key, spk)
            self.assertTrue(await mempool.add_transaction(c2))
            c3 = _build_child(c2.txid().hex(), 997_000, 996_000, key, spk)
            self.assertTrue(await mempool.add_transaction(c3))

            # This extends descendants of root parent to 4 and must be rejected.
            c4 = _build_child(c3.txid().hex(), 996_000, 995_000, key, spk)
            self.assertFalse(await mempool.add_transaction(c4))
            self.assertEqual(mempool.last_reject_reason, "too_many_descendants")
            self.assertNotIn(c4.txid().hex(), mempool.transactions)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
