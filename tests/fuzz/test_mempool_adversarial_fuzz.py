"""Adversarial fuzz corpus for mempool behavior."""

from __future__ import annotations

import asyncio
import os
import random
import struct
import unittest
from typing import Dict, Tuple

from node.mempool.limits import MempoolLimits
from node.mempool.pool import Mempool
from node.mempool.policy import MempoolPolicy
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
from shared.utils.errors import SerializationError


class _ChainStateStub:
    def __init__(self, utxos: Dict[Tuple[str, int], Dict[str, object]], best_height: int = 120):
        self._utxos = dict(utxos)
        self._known = {txid for (txid, _idx) in utxos.keys()}
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
        return self._utxos.get((txid, int(index)))

    def get_best_height(self) -> int:
        return self.best_height


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


class TestMempoolAdversarialFuzz(unittest.TestCase):
    def setUp(self) -> None:
        self.seed = int(os.getenv("BERZ_MEMPOOL_FUZZ_SEED", "20260408"))
        self.samples = int(os.getenv("BERZ_MEMPOOL_FUZZ_SAMPLES", "120"))
        self.rng = random.Random(self.seed)
        self.key = PrivateKey()
        self.script_pubkey = _p2pkh_script(hash160(self.key.public_key().to_bytes()))
        self.utxos = {
            (f"{i + 1:064x}", 0): {"value": 120_000, "script_pubkey": self.script_pubkey}
            for i in range(80)
        }
        self.chainstate = _ChainStateStub(self.utxos)
        self.mempool = Mempool(
            self.chainstate,
            policy=MempoolPolicy(),
            limits=MempoolLimits(max_transactions=140, max_package_count=25, max_package_weight=404_000),
        )

    def _random_blob(self, max_len: int = 512) -> bytes:
        length = self.rng.randint(0, max_len)
        return bytes(self.rng.getrandbits(8) for _ in range(length))

    def test_malformed_transaction_blobs_fail_closed(self) -> None:
        accepted_errors = (
            ValueError,
            SerializationError,
            IndexError,
            OverflowError,
            struct.error,
        )

        for _ in range(self.samples):
            blob = self._random_blob(600)
            try:
                tx, consumed = Transaction.deserialize(blob)
                self.assertLessEqual(consumed, len(blob))
                # If parse succeeds, mempool path should not crash regardless of acceptance.
                asyncio.run(self.mempool.add_transaction(tx))
            except accepted_errors:
                pass

    def test_weird_script_transactions_are_bounded(self) -> None:
        async def run() -> None:
            for i in range(self.samples):
                prev_txid = f"{(i % 80) + 1:064x}"
                tx = Transaction(version=2)
                tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
                tx.vout = [TxOut(self.rng.randint(1, 110_000), self._random_blob(180))]
                # Deliberately random/odd script_sig payloads.
                tx.vin[0].script_sig = self._random_blob(350)
                await self.mempool.add_transaction(tx)
            self.assertLessEqual(len(self.mempool.transactions), self.mempool.limits.max_transactions)
            self.assertLessEqual(int(self.mempool.total_weight), int(self.mempool.limits.max_weight))

        asyncio.run(run())

    def test_extreme_package_graphs_are_predictable_and_bounded(self) -> None:
        async def run() -> None:
            for _ in range(max(25, self.samples // 12)):
                root_prev = f"{self.rng.randint(1, 80):064x}"
                parent = Transaction(version=2)
                parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(root_prev), prev_tx_index=0, sequence=0xFFFFFFFD)]
                parent.vout = [TxOut(105_000, self.script_pubkey)]
                _sign_input(parent, 0, self.key, self.script_pubkey)

                package = [parent]
                prev = parent
                depth = self.rng.randint(5, 35)
                for _d in range(depth):
                    child = Transaction(version=2)
                    child.vin = [TxIn(prev_tx_hash=prev.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
                    child.vout = [TxOut(max(500, prev.vout[0].value - self.rng.randint(400, 2_500)), self.script_pubkey)]
                    _sign_input(child, 0, self.key, self.script_pubkey)
                    package.append(child)
                    prev = child

                shuffled = package[:]
                self.rng.shuffle(shuffled)
                result = await self.mempool.add_package(shuffled)
                if not result.get("accepted"):
                    self.assertIn(
                        result.get("reject-reason"),
                        {
                            "package_too_many_transactions",
                            "package_too_heavy",
                            "package_fee_too_low",
                            "package_topology_invalid",
                            "package_missing_parents",
                            "fee_too_low",
                            "too_many_ancestors",
                            "ancestor_package_too_large",
                            "descendant_package_too_large",
                            "mempool_limits",
                            "rbf_policy",
                            "validation_failed",
                            "missing_parents",
                        },
                    )
            self.assertLessEqual(len(self.mempool.transactions), self.mempool.limits.max_transactions)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
