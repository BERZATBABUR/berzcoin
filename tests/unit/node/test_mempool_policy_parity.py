"""Mempool policy parity regression tests."""

import asyncio
import unittest

from node.mempool.limits import MempoolLimits
from node.mempool.pool import Mempool
from node.mempool.policy import MempoolPolicy
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


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


class TestMempoolPolicyParity(unittest.TestCase):
    def test_package_count_limit(self) -> None:
        async def run() -> None:
            limits = MempoolLimits(max_package_count=1)
            mempool = Mempool(_ChainStateStub({}, set()), limits=limits)
            tx1 = Transaction(version=2, outputs=[TxOut(1, b"\x51")], locktime=1)
            tx2 = Transaction(version=2, outputs=[TxOut(1, b"\x51")], locktime=2)
            result = await mempool.add_package([tx1, tx2])
            self.assertFalse(result.get("accepted"))
            self.assertEqual(result.get("reject-reason"), "package_too_many_transactions")

        asyncio.run(run())

    def test_block_selection_prefers_ancestor_package_feerate(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient = PrivateKey()
            alt = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            recipient_spk = _p2pkh_script(hash160(recipient.public_key().to_bytes()))
            alt_spk = _p2pkh_script(hash160(alt.public_key().to_bytes()))

            prev_a = "aa" * 32
            prev_b = "bb" * 32
            chainstate = _ChainStateStub(
                {
                    (prev_a, 0): {"value": 200_000, "script_pubkey": funding_spk},
                    (prev_b, 0): {"value": 120_000, "script_pubkey": funding_spk},
                },
                {prev_a, prev_b},
            )
            mempool = Mempool(chainstate)

            # Package candidate: low-fee parent + high-fee child.
            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_a), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(190_000, child_spk)]  # fee 10k
            _sign_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(120_000, recipient_spk)]  # fee 70k
            _sign_input(child, 0, child_key, child_spk)

            # Standalone medium fee tx.
            standalone = Transaction(version=2)
            standalone.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_b), prev_tx_index=0, sequence=0xFFFFFFFD)]
            standalone.vout = [TxOut(70_000, alt_spk)]  # fee 50k
            _sign_input(standalone, 0, funding_key, funding_spk)

            self.assertTrue(await mempool.add_transaction(parent))
            self.assertTrue(await mempool.add_transaction(child))
            self.assertTrue(await mempool.add_transaction(standalone))

            chosen = await mempool.get_transactions_for_block(max_weight=1_000_000)
            chosen_txids = [t.txid().hex() for t in chosen]
            self.assertIn(parent.txid().hex(), chosen_txids)
            self.assertIn(child.txid().hex(), chosen_txids)

        asyncio.run(run())

    def test_standardness_opreturn_and_scriptsig_limits(self) -> None:
        policy = MempoolPolicy()

        tx = Transaction(version=2)
        tx.vin = [TxIn(script_sig=b"\x01" * 1700)]
        tx.vout = [TxOut(1000, b"\x51")]
        self.assertFalse(policy.is_standard(tx))

        tx2 = Transaction(version=2)
        tx2.vin = [TxIn(script_sig=b"")]
        tx2.vout = [TxOut(0, bytes([0x6A, 0x01, 0x01])), TxOut(0, bytes([0x6A]))]
        self.assertFalse(policy.is_standard(tx2))

        tx3 = Transaction(version=2)
        tx3.vin = [TxIn(script_sig=b"")]
        tx3.vout = [TxOut(1, bytes([0x6A]))]
        self.assertFalse(policy.is_standard(tx3))

    def test_rejects_duplicate_inputs_in_single_transaction(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev_txid = "cc" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                {prev_txid},
            )
            mempool = Mempool(chainstate)

            tx = Transaction(version=2)
            tx.vin = [
                TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD),
                TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD),
            ]
            tx.vout = [TxOut(50_000, spk)]
            _sign_input(tx, 0, key, spk)
            _sign_input(tx, 1, key, spk)

            self.assertFalse(await mempool.add_transaction(tx))
            self.assertEqual(mempool.last_reject_reason, "validation_failed")

        asyncio.run(run())

    def test_rejects_negative_fee_when_outputs_exceed_inputs(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev_txid = "dd" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                {prev_txid},
            )
            mempool = Mempool(chainstate)

            tx = Transaction(version=2)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(100_001, spk)]
            _sign_input(tx, 0, key, spk)

            self.assertFalse(await mempool.add_transaction(tx))
            self.assertEqual(mempool.last_reject_reason, "inputs_less_than_outputs")

        asyncio.run(run())

    def test_replacement_rejects_lower_package_feerate(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev_txid = "ee" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                {prev_txid},
            )
            mempool = Mempool(chainstate)

            old_tx = Transaction(version=2)
            old_tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            old_tx.vout = [TxOut(90_000, spk)]  # fee=10_000
            _sign_input(old_tx, 0, key, spk)

            new_tx = Transaction(version=2)
            new_tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            # Larger replacement (many outputs) but only modest fee bump.
            new_tx.vout = [TxOut(8_900, spk) for _ in range(10)]  # total=89_000, fee=11_000
            _sign_input(new_tx, 0, key, spk)

            self.assertTrue(await mempool.add_transaction(old_tx))
            self.assertFalse(await mempool.add_transaction(new_tx))
            self.assertEqual(mempool.last_reject_reason, "rbf_policy")

        asyncio.run(run())

    def test_ancestor_package_size_limit(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            recipient_spk = _p2pkh_script(hash160(recipient.public_key().to_bytes()))

            prev_txid = "ab" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 200_000, "script_pubkey": funding_spk}},
                {prev_txid},
            )
            limits = MempoolLimits(max_ancestor_size_vbytes=350)
            mempool = Mempool(chainstate, limits=limits)

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(150_000, child_spk)]
            _sign_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(120_000, recipient_spk)]
            _sign_input(child, 0, child_key, child_spk)

            self.assertTrue(await mempool.add_transaction(parent))
            self.assertFalse(await mempool.add_transaction(child))
            self.assertEqual(mempool.last_reject_reason, "ancestor_package_too_large")

        asyncio.run(run())

    def test_descendant_package_size_limit(self) -> None:
        async def run() -> None:
            funding_key = PrivateKey()
            child_key = PrivateKey()
            recipient = PrivateKey()

            funding_spk = _p2pkh_script(hash160(funding_key.public_key().to_bytes()))
            child_spk = _p2pkh_script(hash160(child_key.public_key().to_bytes()))
            recipient_spk = _p2pkh_script(hash160(recipient.public_key().to_bytes()))

            prev_txid = "ac" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 200_000, "script_pubkey": funding_spk}},
                {prev_txid},
            )
            limits = MempoolLimits(max_descendant_size_vbytes=350)
            mempool = Mempool(chainstate, limits=limits)

            parent = Transaction(version=2)
            parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            parent.vout = [TxOut(150_000, child_spk)]
            _sign_input(parent, 0, funding_key, funding_spk)

            child = Transaction(version=2)
            child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
            child.vout = [TxOut(120_000, recipient_spk)]
            _sign_input(child, 0, child_key, child_spk)

            self.assertTrue(await mempool.add_transaction(parent))
            self.assertFalse(await mempool.add_transaction(child))
            self.assertEqual(mempool.last_reject_reason, "descendant_package_too_large")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
