"""Deterministic adversarial mempool chaos simulator."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from node.mempool.limits import MempoolLimits
from node.mempool.pool import Mempool
from node.mempool.policy import MempoolPolicy
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


@dataclass
class MempoolChaosMetrics:
    seed: int
    steps: int
    crashes: int
    consensus_drift: bool
    reject_reasons: Dict[str, int]
    eviction_reasons: Dict[str, int]
    mempool_growth: List[Dict[str, int]]
    peak_mempool_size: int
    peak_mempool_vsize: int


class _ChainStateStub:
    def __init__(self, utxos: Dict[Tuple[str, int], Dict[str, object]], best_height: int = 200):
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

    def invalidate_some_utxos(self, rng: random.Random, ratio: float = 0.25) -> None:
        keys = list(self._utxos.keys())
        if not keys:
            return
        remove_count = max(1, int(len(keys) * max(0.0, min(1.0, ratio))))
        for outpoint in rng.sample(keys, k=min(remove_count, len(keys))):
            self._utxos.pop(outpoint, None)


class _BlockStub:
    def __init__(self):
        self.transactions: List[Transaction] = []


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, index: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, index, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[index].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


def _build_funding_utxos(key: PrivateKey, count: int = 250, value: int = 120_000) -> Dict[Tuple[str, int], Dict[str, object]]:
    spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
    utxos: Dict[Tuple[str, int], Dict[str, object]] = {}
    for i in range(int(count)):
        txid = f"{(i + 1):064x}"
        utxos[(txid, 0)] = {"value": int(value), "script_pubkey": spk}
    return utxos


def run_mempool_chaos_simulation(seed: int, steps: int = 700) -> MempoolChaosMetrics:
    rng = random.Random(int(seed))
    key = PrivateKey()
    script_pubkey = _p2pkh_script(hash160(key.public_key().to_bytes()))
    funding = _build_funding_utxos(key)
    chainstate = _ChainStateStub(funding)
    limits = MempoolLimits(max_transactions=180, max_size=220_000, max_weight=900_000)
    policy = MempoolPolicy()
    mempool = Mempool(chainstate, policy=policy, limits=limits)

    available_outpoints: List[Tuple[str, int, int]] = [
        (txid, idx, int(meta["value"]))
        for (txid, idx), meta in funding.items()
    ]
    crashes = 0
    peak_size = 0
    peak_vsize = 0
    mempool_growth: List[Dict[str, int]] = []

    async def submit_spend(
        prev_txid: str,
        prev_index: int,
        prev_value: int,
        spend_value: int,
        sequence: int = 0xFFFFFFFD,
    ) -> bool:
        tx = Transaction(version=2)
        tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=int(prev_index), sequence=int(sequence))]
        tx.vout = [TxOut(int(max(0, spend_value)), script_pubkey)]
        _sign_input(tx, 0, key, script_pubkey)
        return await mempool.add_transaction(tx)

    async def run_async() -> None:
        nonlocal crashes, peak_size, peak_vsize
        for step in range(int(steps)):
            event = rng.choice(
                [
                    "conflict_storm",
                    "double_spend_flood",
                    "reorg_pressure",
                    "package_spike",
                    "normal",
                ]
            )
            try:
                if event == "conflict_storm" and available_outpoints:
                    base_txid, base_idx, base_value = rng.choice(available_outpoints)
                    fee_levels = [2_000, 8_000, 12_000, 18_000, 26_000]
                    for fee in fee_levels:
                        spend_value = max(1_000, base_value - fee)
                        await submit_spend(base_txid, base_idx, base_value, spend_value, sequence=0xFFFFFFFD)

                elif event == "double_spend_flood" and available_outpoints:
                    picks = rng.sample(available_outpoints, k=min(len(available_outpoints), rng.randint(8, 20)))
                    for prev_txid, prev_idx, prev_value in picks:
                        low_fee_value = max(500, prev_value - rng.randint(500, 2_000))
                        await submit_spend(prev_txid, prev_idx, prev_value, low_fee_value, sequence=0xFFFFFFFF)
                        # Immediate conflicting submission.
                        await submit_spend(prev_txid, prev_idx, prev_value, max(500, low_fee_value - 1_000), sequence=0xFFFFFFFF)

                elif event == "reorg_pressure":
                    # Fill some pressure first.
                    for _ in range(rng.randint(3, 9)):
                        if not available_outpoints:
                            break
                        prev_txid, prev_idx, prev_value = rng.choice(available_outpoints)
                        await submit_spend(prev_txid, prev_idx, prev_value, max(2_000, prev_value - rng.randint(3_000, 15_000)))
                    # Simulate reorg that invalidates a chunk of UTXOs.
                    chainstate.best_height += 1
                    chainstate.invalidate_some_utxos(rng, ratio=0.15)
                    await mempool.handle_connected_block(_BlockStub())

                elif event == "package_spike" and available_outpoints:
                    base_txid, base_idx, base_value = rng.choice(available_outpoints)
                    parent = Transaction(version=2)
                    parent.vin = [TxIn(prev_tx_hash=bytes.fromhex(base_txid), prev_tx_index=base_idx, sequence=0xFFFFFFFD)]
                    parent.vout = [TxOut(max(20_000, base_value - 9_000), script_pubkey)]
                    _sign_input(parent, 0, key, script_pubkey)
                    child = Transaction(version=2)
                    child.vin = [TxIn(prev_tx_hash=parent.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
                    child.vout = [TxOut(max(10_000, parent.vout[0].value - 11_000), script_pubkey)]
                    _sign_input(child, 0, key, script_pubkey)
                    grand = Transaction(version=2)
                    grand.vin = [TxIn(prev_tx_hash=child.txid(), prev_tx_index=0, sequence=0xFFFFFFFD)]
                    grand.vout = [TxOut(max(2_000, child.vout[0].value - 13_000), script_pubkey)]
                    _sign_input(grand, 0, key, script_pubkey)
                    await mempool.add_package([grand, child, parent])

                else:  # normal
                    if not available_outpoints:
                        continue
                    prev_txid, prev_idx, prev_value = rng.choice(available_outpoints)
                    await submit_spend(prev_txid, prev_idx, prev_value, max(2_000, prev_value - rng.randint(2_000, 8_000)))

                cur_size = len(mempool.transactions)
                cur_vsize = int(mempool.total_vsize)
                peak_size = max(peak_size, cur_size)
                peak_vsize = max(peak_vsize, cur_vsize)
                mempool_growth.append({"step": int(step), "size": int(cur_size), "vsize": int(cur_vsize)})

            except Exception:
                crashes += 1
                raise

    asyncio.run(run_async())

    consensus_drift = False
    for txid, entry in list(mempool.transactions.items()):
        tx = entry.tx
        ignored = {(txin.prev_tx_hash.hex(), int(txin.prev_tx_index)) for txin in tx.vin}
        valid = asyncio.run(mempool._validate_transaction(tx, ignored_spent_outpoints=ignored))
        if not valid:
            consensus_drift = True
            break

    return MempoolChaosMetrics(
        seed=int(seed),
        steps=int(steps),
        crashes=int(crashes),
        consensus_drift=bool(consensus_drift),
        reject_reasons=dict(mempool.reject_reason_counts),
        eviction_reasons=dict(mempool.eviction_reason_counts),
        mempool_growth=list(mempool_growth),
        peak_mempool_size=int(peak_size),
        peak_mempool_vsize=int(peak_vsize),
    )
