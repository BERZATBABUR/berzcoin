"""Deterministic chaos simulation harness for nightly reliability checks."""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from node.chain.reorg import ReorgManager
from node.p2p.peer_scoring import PeerScoringManager


@dataclass
class ChaosMetrics:
    seed: int
    steps: int
    crashes: int
    consensus_divergence: bool
    rejection_reasons: Dict[str, int]
    peer_stats: Dict[str, int]
    mempool_growth: List[Dict[str, int]]
    tip_convergence_max_steps: int
    max_reorg_depth: int
    reject_rate: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "seed": self.seed,
            "steps": self.steps,
            "crashes": self.crashes,
            "consensus_divergence": self.consensus_divergence,
            "rejection_reasons": dict(self.rejection_reasons),
            "peer_stats": dict(self.peer_stats),
            "mempool_growth": list(self.mempool_growth),
            "tip_convergence_max_steps": self.tip_convergence_max_steps,
            "max_reorg_depth": self.max_reorg_depth,
            "reject_rate": self.reject_rate,
        }


class _Header:
    def __init__(self, block_hash: str, prev_hash: str):
        self._block_hash = block_hash
        self.prev_block_hash = bytes.fromhex(prev_hash)

    def hash_hex(self) -> str:
        return self._block_hash


class _Entry:
    def __init__(self, height: int, block_hash: str, prev_hash: str):
        self.height = height
        self.block_hash = block_hash
        self.header = _Header(block_hash, prev_hash)
        self.chainwork = height
        self.status = 0

    def is_main_chain(self) -> bool:
        return bool(self.status & 0x01)


class _Block:
    def __init__(self, block_hash: str, prev_hash: str):
        self.header = _Header(block_hash, prev_hash)


class _BlockIndex:
    def __init__(self, entries):
        self.entries = entries
        self._best_hash = None

    def get_block(self, block_hash: str):
        return self.entries.get(block_hash)

    def set_best_chain_tip(self, block_hash: str):
        self._best_hash = block_hash
        chain_hashes = set()
        current = self.entries.get(block_hash)
        while current is not None:
            chain_hashes.add(current.block_hash)
            current = self.entries.get(current.header.prev_block_hash.hex())
        for entry in self.entries.values():
            if entry.block_hash in chain_hashes:
                entry.status |= 0x01
            else:
                entry.status &= ~0x01

    def mark_main_chain(self, block_hash: str, is_main: bool = True):
        ent = self.entries.get(block_hash)
        if not ent:
            return
        if is_main:
            ent.status |= 0x01
        else:
            ent.status &= ~0x01


class _TxDB:
    def transaction(self):
        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


class _UTXOStore:
    def __init__(self):
        self.db = _TxDB()


class _Connect:
    def __init__(self, rng: random.Random, fail_ratio: float):
        self.rng = rng
        self.fail_ratio = fail_ratio

    def connect(self, _block):
        return self.rng.random() >= self.fail_ratio


class _Disconnect:
    def __init__(self, rng: random.Random, fail_ratio: float):
        self.rng = rng
        self.fail_ratio = fail_ratio

    def disconnect(self, _block):
        return self.rng.random() >= self.fail_ratio


def _mk_chain(prefix: str, start: int, end: int, parent_hash: str, entries):
    prev = parent_hash
    for h in range(start, end + 1):
        bh = (prefix + f"{h:06d}")[:64].ljust(64, "0")
        entries[bh] = _Entry(h, bh, prev)
        prev = bh
    return entries[prev]


def _simulate_reorg_round(rng: random.Random) -> tuple[bool, int]:
    entries = {}
    fork = ("aa" + f"{rng.getrandbits(248):062x}")[:64]
    entries[fork] = _Entry(10, fork, "00" * 32)

    old_best = _mk_chain("bb", 11, 11 + rng.randint(1, 6), fork, entries)
    new_best = _mk_chain("cc", 11, 11 + rng.randint(1, 6), fork, entries)

    idx = _BlockIndex(entries)
    utxo = _UTXOStore()
    mgr = ReorgManager(utxo, idx, max_reorg_depth=128)
    mgr.connect_block = _Connect(rng, fail_ratio=0.10)
    mgr.disconnect_block = _Disconnect(rng, fail_ratio=0.03)

    cur = old_best
    while cur and cur.height > 10:
        idx.mark_main_chain(cur.block_hash, True)
        cur = idx.get_block(cur.header.prev_block_hash.hex())

    mgr.reorganize(
        new_best,
        old_best,
        get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
    )

    old_tip_main = entries[old_best.block_hash].is_main_chain()
    new_tip_main = entries[new_best.block_hash].is_main_chain()
    max_depth = max(int(old_best.height) - 10, int(new_best.height) - 10)
    return bool(old_tip_main and new_tip_main), int(max_depth)


def run_chaos_simulation(
    seed: int,
    steps: int = 300,
    data_dir: Optional[Path] = None,
    peer_count: int = 15,
) -> ChaosMetrics:
    rng = random.Random(int(seed))
    crashes = 0
    divergence = False
    rejection_reasons: Dict[str, int] = {}
    max_reorg_depth = 0
    convergence_events: List[int] = []
    convergence_pending: Optional[int] = None
    convergence_target: Optional[int] = None

    if data_dir is None:
        temp = tempfile.TemporaryDirectory()
        owned_tmp = temp
        data_dir = Path(temp.name)
    else:
        owned_tmp = None

    scoring = PeerScoringManager(network_hardening=True)
    scoring.configure_persistence(Path(data_dir))

    normalized_peers = max(4, int(peer_count))
    peers = {f"198.51.100.{i}:8333" for i in range(1, normalized_peers + 1)}
    partitioned = False
    mempool = 0
    mempool_growth: List[Dict[str, int]] = []

    def bump_reject(reason: str) -> None:
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    try:
        for step in range(int(steps)):
            event = rng.choice(
                [
                    "partition_toggle",
                    "reconnect_storm",
                    "inv_spam",
                    "stale_churn",
                    "reorg_under_load",
                    "restart_storm",
                    "normal",
                ]
            )

            try:
                if event == "partition_toggle":
                    partitioned = not partitioned
                    if partitioned:
                        for addr in rng.sample(sorted(peers), k=min(4, len(peers))):
                            scoring.record_bad(addr, "connect_failed")
                            bump_reject("partition_disconnect")
                    else:
                        convergence_pending = rng.randint(1, max(3, min(80, int(steps / 16) + 1)))
                        convergence_target = int(convergence_pending)

                elif event == "reconnect_storm":
                    for _ in range(rng.randint(8, 22)):
                        addr = rng.choice(sorted(peers))
                        if rng.random() < 0.6:
                            scoring.record_bad(addr, "connect_failed")
                            bump_reject("connect_failed")
                        else:
                            scoring.record_good(addr)

                elif event == "inv_spam":
                    for _ in range(rng.randint(10, 40)):
                        addr = rng.choice(sorted(peers))
                        scoring.record_bad(addr, "relay_spam")
                    bump_reject("invalid_inventory")

                elif event == "stale_churn":
                    leaves = rng.sample(sorted(peers), k=min(3, len(peers)))
                    for addr in leaves:
                        scoring.record_bad(addr, "stale_peer")
                        peers.discard(addr)
                    while len(peers) < 8:
                        host = rng.randint(20, 240)
                        peers.add(f"203.0.113.{host}:8333")

                elif event == "reorg_under_load":
                    reorg_divergence, depth = _simulate_reorg_round(rng)
                    divergence = divergence or reorg_divergence
                    max_reorg_depth = max(max_reorg_depth, int(depth))
                    for _ in range(rng.randint(4, 12)):
                        addr = rng.choice(sorted(peers))
                        if rng.random() < 0.2:
                            scoring.record_bad(addr, "protocol_violation")

                elif event == "restart_storm":
                    restart_target = rng.sample(sorted(peers), k=min(max(1, len(peers) // 2), len(peers)))
                    for addr in restart_target:
                        scoring.record_bad(addr, "connect_failed")
                        bump_reject("restart_reconnect")
                    # Reopen the score manager from persisted state to mimic restart behavior.
                    del scoring
                    scoring = PeerScoringManager(network_hardening=True)
                    scoring.configure_persistence(Path(data_dir))

                else:  # normal
                    for _ in range(rng.randint(2, 8)):
                        scoring.record_good(rng.choice(sorted(peers)))

                ingress = rng.randint(0, 40)
                egress = rng.randint(0, 35)
                if partitioned:
                    ingress += rng.randint(5, 25)
                mempool = max(0, mempool + ingress - egress)
                mempool = min(mempool, 250_000)
                mempool_growth.append({"step": step, "size": int(mempool)})
                if convergence_pending is not None:
                    convergence_pending -= 1
                    if convergence_pending <= 0:
                        convergence_events.append(max(1, int(convergence_target or 1)))
                        convergence_pending = None
                        convergence_target = None

            except Exception:
                crashes += 1
                raise

    finally:
        if owned_tmp is not None:
            owned_tmp.cleanup()

    stats = scoring.get_stats()
    stats["active_peers"] = len(peers)
    total_rejects = int(sum(int(v) for v in rejection_reasons.values()))
    # Normalize to a bounded 0..1 pressure ratio instead of raw rejects/step.
    reject_rate = min(1.0, float(total_rejects) / float(max(1, int(steps) * 40)))
    tip_convergence = int(max(convergence_events)) if convergence_events else 0

    return ChaosMetrics(
        seed=int(seed),
        steps=int(steps),
        crashes=int(crashes),
        consensus_divergence=bool(divergence),
        rejection_reasons=rejection_reasons,
        peer_stats={k: int(v) for k, v in stats.items()},
        mempool_growth=mempool_growth,
        tip_convergence_max_steps=tip_convergence,
        max_reorg_depth=int(max_reorg_depth),
        reject_rate=reject_rate,
    )
