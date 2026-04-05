"""Long-running fault-injection / soak tests (opt-in)."""

import os
import random
import unittest
from contextlib import contextmanager

from node.chain.reorg import ReorgManager
from tests.chaos.artifacts import write_json_artifact


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

    def get_best_hash(self):
        return self._best_hash

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
            if self._best_hash is None or ent.height >= self.entries[self._best_hash].height:
                self._best_hash = block_hash
        else:
            ent.status &= ~0x01


class _TxDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise


class _UTXOStore:
    def __init__(self):
        self.db = _TxDB()


class _Connect:
    def __init__(self, fail_ratio: float = 0.0):
        self.fail_ratio = fail_ratio

    def connect(self, block):
        _ = block
        return random.random() >= self.fail_ratio


class _Disconnect:
    def __init__(self, fail_ratio: float = 0.0):
        self.fail_ratio = fail_ratio

    def disconnect(self, block):
        _ = block
        return random.random() >= self.fail_ratio


def _mk_chain(prefix: str, start: int, end: int, parent_hash: str, entries):
    prev = parent_hash
    for h in range(start, end + 1):
        bh = (prefix + f"{h:06d}")[:64].ljust(64, "0")
        entries[bh] = _Entry(h, bh, prev)
        prev = bh
    return entries[prev]


@unittest.skipUnless(os.getenv("BERZ_SOAK", "0") == "1", "set BERZ_SOAK=1 to run long soak tests")
class TestFaultInjectionSoak(unittest.TestCase):
    def test_reorg_fault_injection_soak(self) -> None:
        iterations = int(os.getenv("BERZ_SOAK_ITERS", "400"))
        rng_seed = int(os.getenv("BERZ_SOAK_SEED", "1337"))
        random.seed(rng_seed)
        ok_count = 0
        fail_count = 0

        for i in range(iterations):
            entries = {}
            fork = ("aa" + f"{i:062x}")[:64]
            entries[fork] = _Entry(10, fork, "00" * 32)

            old_best = _mk_chain("bb", 11, 11 + random.randint(1, 6), fork, entries)
            new_best = _mk_chain("cc", 11, 11 + random.randint(1, 6), fork, entries)

            idx = _BlockIndex(entries)
            utxo = _UTXOStore()
            mgr = ReorgManager(utxo, idx, max_reorg_depth=128)
            mgr.connect_block = _Connect(fail_ratio=0.20)
            mgr.disconnect_block = _Disconnect(fail_ratio=0.05)

            # Mark old branch as active precondition.
            cur = old_best
            while cur and cur.height > 10:
                idx.mark_main_chain(cur.block_hash, True)
                cur = idx.get_block(cur.header.prev_block_hash.hex())

            ok, _disconnected, _connected = mgr.reorganize(
                new_best,
                old_best,
                get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
            )

            # Invariants: no crash, tip branches are mutually exclusive in main chain flags.
            old_tip_main = entries[old_best.block_hash].is_main_chain()
            new_tip_main = entries[new_best.block_hash].is_main_chain()
            self.assertFalse(old_tip_main and new_tip_main)
            if ok:
                self.assertTrue(new_tip_main)
                ok_count += 1
            else:
                self.assertTrue(utxo.db.rollbacks >= 1)
                fail_count += 1

        write_json_artifact(
            "chaos/fault_injection_soak_summary.json",
            {
                "seed": rng_seed,
                "iterations": iterations,
                "ok_reorgs": ok_count,
                "failed_reorgs": fail_count,
            },
        )


if __name__ == "__main__":
    unittest.main()
