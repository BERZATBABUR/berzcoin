"""P0 mempool safety/parity lock tests: telemetry + activation/reorg cleanup."""

import asyncio
import unittest

from node.mempool.limits import MempoolLimits
from node.mempool.pool import Mempool
from shared.consensus.buried_deployments import HARDFORK_TX_V2
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _ChainStateStub:
    def __init__(self, utxos, known_txids, best_height: int = 100, custom_activation_heights=None):
        self._utxos = dict(utxos)
        self._known = set(known_txids)
        self.best_height = int(best_height)
        self.params = type(
            "Params",
            (),
            {
                "coinbase_maturity": 100,
                "max_money": 21_000_000 * 100_000_000,
                "custom_activation_heights": dict(custom_activation_heights or {}),
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


class _BlockStub:
    def __init__(self, txs):
        self.transactions = txs


class TestMempoolSafetyLock(unittest.TestCase):
    def test_reject_reason_counter_tracks_consensus_reject(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev_txid = "fa" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                {prev_txid},
                best_height=100,
                custom_activation_heights={HARDFORK_TX_V2: 101},
            )
            mempool = Mempool(chainstate)

            tx = Transaction(version=1)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(90_000, spk)]
            _sign_input(tx, 0, key, spk)

            self.assertFalse(await mempool.add_transaction(tx))
            self.assertEqual(mempool.last_reject_reason, "consensus_tx_version_too_low")
            self.assertGreaterEqual(mempool.reject_reason_counts.get("consensus_tx_version_too_low", 0), 1)

        asyncio.run(run())

    def test_eviction_counter_and_floor_gauge_under_pressure(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev1 = "01" * 32
            prev2 = "02" * 32
            chainstate = _ChainStateStub(
                {
                    (prev1, 0): {"value": 100_000, "script_pubkey": spk},
                    (prev2, 0): {"value": 100_000, "script_pubkey": spk},
                },
                {prev1, prev2},
            )
            limits = MempoolLimits(max_transactions=1)
            mempool = Mempool(chainstate, limits=limits)

            low_fee = Transaction(version=2)
            low_fee.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev1), prev_tx_index=0, sequence=0xFFFFFFFD)]
            low_fee.vout = [TxOut(99_000, spk)]  # fee=1_000
            _sign_input(low_fee, 0, key, spk)
            self.assertTrue(await mempool.add_transaction(low_fee))
            low_rate = mempool.transactions[low_fee.txid().hex()].fee_rate

            high_fee = Transaction(version=2)
            high_fee.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev2), prev_tx_index=0, sequence=0xFFFFFFFD)]
            high_fee.vout = [TxOut(80_000, spk)]  # fee=20_000
            _sign_input(high_fee, 0, key, spk)
            self.assertTrue(await mempool.add_transaction(high_fee))

            self.assertIn(high_fee.txid().hex(), mempool.transactions)
            self.assertNotIn(low_fee.txid().hex(), mempool.transactions)
            self.assertGreaterEqual(mempool.eviction_reason_counts.get("mempool_space", 0), 1)
            self.assertGreaterEqual(float(mempool.min_fee_floor_rate), float(low_rate))

        asyncio.run(run())

    def test_activation_boundary_revalidation_evicts_old_version_on_connected_block(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            spk = _p2pkh_script(hash160(pub))
            prev_txid = "03" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                {prev_txid},
                best_height=99,
                custom_activation_heights={HARDFORK_TX_V2: 101},
            )
            mempool = Mempool(chainstate)

            tx = Transaction(version=1)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(90_000, spk)]
            _sign_input(tx, 0, key, spk)

            self.assertTrue(await mempool.add_transaction(tx))
            self.assertIn(tx.txid().hex(), mempool.transactions)

            # Next block after this connected block is height 101, where v2 becomes mandatory.
            chainstate.best_height = 100
            removed = await mempool.handle_connected_block(_BlockStub([]))
            self.assertIn(tx.txid().hex(), removed)
            self.assertNotIn(tx.txid().hex(), mempool.transactions)
            self.assertGreaterEqual(mempool.eviction_reason_counts.get("reorg_revalidation_invalid", 0), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
