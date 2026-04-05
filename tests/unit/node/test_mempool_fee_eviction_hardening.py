"""P1 fee-market and eviction hardening regression tests."""

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
    def __init__(self, utxos, known_txids, best_height: int = 100):
        self._utxos = dict(utxos)
        self._known = set(known_txids)
        self.best_height = int(best_height)
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
        return self.best_height


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


def _make_tx(prev_txid: str, spend_value: int, fee: int, key: PrivateKey, spk: bytes) -> Transaction:
    tx = Transaction(version=2)
    tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
    tx.vout = [TxOut(spend_value - int(fee), spk)]
    _sign_input(tx, 0, key, spk)
    return tx


class TestMempoolFeeEvictionHardening(unittest.TestCase):
    def test_rolling_min_fee_floor_rises_under_pressure(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prevs = [f"{i:064x}" for i in range(1, 5)]
            utxos = {(p, 0): {"value": 100_000, "script_pubkey": spk} for p in prevs}
            cs = _ChainStateStub(utxos, set(prevs))
            mempool = Mempool(cs, limits=MempoolLimits(max_transactions=2))

            tx1 = _make_tx(prevs[0], 100_000, fee=1_000, key=key, spk=spk)
            tx2 = _make_tx(prevs[1], 100_000, fee=1_100, key=key, spk=spk)
            self.assertTrue(await mempool.add_transaction(tx1))
            self.assertTrue(await mempool.add_transaction(tx2))

            tx3 = _make_tx(prevs[2], 100_000, fee=20_000, key=key, spk=spk)
            self.assertTrue(await mempool.add_transaction(tx3))
            self.assertGreater(float(mempool.min_fee_floor_rate), 1.0)

            tx4 = _make_tx(prevs[3], 100_000, fee=1, key=key, spk=spk)
            self.assertFalse(await mempool.add_transaction(tx4))
            self.assertEqual(mempool.last_reject_reason, "fee_too_low")
            self.assertGreaterEqual(mempool.reject_reason_counts.get("fee_too_low", 0), 1)

        asyncio.run(run())

    def test_deterministic_tie_breaker_for_equal_fee_candidates(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prevs = [f"{i:064x}" for i in range(11, 15)]
            utxos = {(p, 0): {"value": 100_000, "script_pubkey": spk} for p in prevs}
            cs = _ChainStateStub(utxos, set(prevs))
            mempool = Mempool(cs, limits=MempoolLimits(max_transactions=2))

            a = _make_tx(prevs[0], 100_000, fee=1_000, key=key, spk=spk)
            b = _make_tx(prevs[1], 100_000, fee=1_000, key=key, spk=spk)
            self.assertTrue(await mempool.add_transaction(a))
            self.assertTrue(await mempool.add_transaction(b))
            a_id = a.txid().hex()
            b_id = b.txid().hex()
            # Signatures may yield different vsize for same absolute fee, so use the
            # mempool's deterministic eviction rank instead of raw txid ordering.
            expected_evicted = sorted([a_id, b_id], key=mempool._eviction_rank)[0]

            incoming = _make_tx(prevs[2], 100_000, fee=15_000, key=key, spk=spk)
            self.assertTrue(await mempool.add_transaction(incoming))
            self.assertNotIn(expected_evicted, mempool.transactions)
            self.assertGreaterEqual(mempool.eviction_reason_counts.get("mempool_space", 0), 1)

        asyncio.run(run())

    def test_package_impact_protects_high_value_parent_child_cluster(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            alt_key = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            alt_spk = _p2pkh_script(hash160(alt_key.public_key().to_bytes()))

            prev_a = "aa" * 32
            prev_b = "bb" * 32
            prev_c = "cc" * 32
            cs = _ChainStateStub(
                {
                    (prev_a, 0): {"value": 200_000, "script_pubkey": funding_spk},
                    (prev_b, 0): {"value": 120_000, "script_pubkey": funding_spk},
                    (prev_c, 0): {"value": 120_000, "script_pubkey": funding_spk},
                },
                {prev_a, prev_b, prev_c},
            )
            mempool = Mempool(cs, limits=MempoolLimits(max_transactions=3))

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_a), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(190_000, child_spk)]  # fee 10k
            _sign_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(120_000, alt_spk)]  # fee 70k
            _sign_input(child, 0, child_key, child_spk)

            standalone_low = Transaction(version=2)
            standalone_low.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_b), prev_tx_index=0, sequence=0xFFFFFFFD)]
            standalone_low.vout = [TxOut(118_000, alt_spk)]  # fee 2k
            _sign_input(standalone_low, 0, funding_key, funding_spk)

            incoming = Transaction(version=2)
            incoming.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_c), prev_tx_index=0, sequence=0xFFFFFFFD)]
            incoming.vout = [TxOut(100_000, alt_spk)]  # fee 20k
            _sign_input(incoming, 0, funding_key, funding_spk)

            self.assertTrue(await mempool.add_transaction(parent))
            self.assertTrue(await mempool.add_transaction(child))
            self.assertTrue(await mempool.add_transaction(standalone_low))
            self.assertTrue(await mempool.add_transaction(incoming))

            self.assertIn(parent.txid().hex(), mempool.transactions)
            self.assertIn(child.txid().hex(), mempool.transactions)
            self.assertNotIn(standalone_low.txid().hex(), mempool.transactions)

        asyncio.run(run())

    def test_high_load_spam_burst_high_fee_outlives_low_fee(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            low_count = 24
            high_count = 12
            prevs = [f"{i:064x}" for i in range(200, 200 + low_count + high_count)]
            utxos = {(p, 0): {"value": 100_000, "script_pubkey": spk} for p in prevs}
            cs = _ChainStateStub(utxos, set(prevs))
            mempool = Mempool(cs, limits=MempoolLimits(max_transactions=low_count))

            low_txids = []
            for i in range(low_count):
                tx = _make_tx(prevs[i], 100_000, fee=900 + (i % 3), key=key, spk=spk)
                self.assertTrue(await mempool.add_transaction(tx))
                low_txids.append(tx.txid().hex())

            high_txids = []
            for i in range(low_count, low_count + high_count):
                tx = _make_tx(prevs[i], 100_000, fee=25_000 + i, key=key, spk=spk)
                self.assertTrue(await mempool.add_transaction(tx))
                high_txids.append(tx.txid().hex())

            survivors_high = sum(1 for tid in high_txids if tid in mempool.transactions)
            survivors_low = sum(1 for tid in low_txids if tid in mempool.transactions)

            self.assertEqual(len(mempool.transactions), low_count)
            self.assertEqual(survivors_high, high_count)
            self.assertLess(survivors_low, low_count)
            self.assertGreaterEqual(mempool.eviction_reason_counts.get("mempool_space", 0), high_count)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
