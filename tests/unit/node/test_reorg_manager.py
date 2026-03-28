"""Unit tests for reorg safety and rollback behavior."""

import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from node.chain.reorg import ReorgManager


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


class _BlockIndexStub:
    def __init__(self, entries):
        self.entries = entries
        self.main_chain = {}
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
        self.main_chain[block_hash] = is_main
        entry = self.entries.get(block_hash)
        if entry is None:
            return
        if is_main:
            entry.status |= 0x01
        else:
            entry.status &= ~0x01


class _TxDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.connection = self

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise

    def execute(self, _sql):
        return None


class _UTXOStoreStub:
    def __init__(self):
        self.db = _TxDB()


class _ConnectStub:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.calls = []

    def connect(self, block):
        h = block.header.hash_hex()
        self.calls.append(h)
        return h not in self.fail_on


class _DisconnectStub:
    def __init__(self):
        self.calls = []

    def disconnect(self, block):
        self.calls.append(block.header.hash_hex())
        return True


class TestReorgManager(unittest.TestCase):
    def _mk_chain(self, prefix: str, start: int, end: int, parent_hash: str, entries):
        prev = parent_hash
        for h in range(start, end + 1):
            bh = (prefix + f"{h:06d}")[:64].ljust(64, "0")
            entries[bh] = _Entry(h, bh, prev)
            prev = bh
        return entries[prev]

    def test_reorg_depth_limit(self):
        entries = {}
        fork = "aa" * 32
        entries[fork] = _Entry(100, fork, "00" * 32)
        old_best = self._mk_chain("bb", 101, 180, fork, entries)
        new_best = self._mk_chain("cc", 101, 120, fork, entries)

        idx = _BlockIndexStub(entries)
        mgr = ReorgManager(_UTXOStoreStub(), idx, max_reorg_depth=32)
        mgr.connect_block = _ConnectStub()
        mgr.disconnect_block = _DisconnectStub()

        ok, disconnected, connected = mgr.reorganize(
            new_best,
            old_best,
            get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
        )
        self.assertFalse(ok)
        self.assertEqual(disconnected, [])
        self.assertEqual(connected, [])

    def test_rollback_restores_old_chain_flags(self):
        entries = {}
        fork = "11" * 32
        entries[fork] = _Entry(10, fork, "00" * 32)
        old1 = self._mk_chain("22", 11, 11, fork, entries)
        old2 = self._mk_chain("23", 12, 12, old1.block_hash, entries)
        new1 = self._mk_chain("33", 11, 11, fork, entries)
        new2 = self._mk_chain("34", 12, 12, new1.block_hash, entries)

        idx = _BlockIndexStub(entries)
        utxo = _UTXOStoreStub()
        mgr = ReorgManager(utxo, idx, max_reorg_depth=144)
        conn = _ConnectStub(fail_on={new2.block_hash})
        disc = _DisconnectStub()
        mgr.connect_block = conn
        mgr.disconnect_block = disc
        idx.mark_main_chain(old1.block_hash, True)
        idx.mark_main_chain(old2.block_hash, True)
        idx.mark_main_chain(new1.block_hash, False)
        idx.mark_main_chain(new2.block_hash, False)
        idx.set_best_chain_tip(old2.block_hash)

        ok, disconnected, connected = mgr.reorganize(
            new2,
            old2,
            get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
        )
        self.assertFalse(ok)
        self.assertEqual(disconnected, [])
        self.assertEqual(connected, [])
        self.assertTrue(entries[old1.block_hash].is_main_chain())
        self.assertTrue(entries[old2.block_hash].is_main_chain())
        self.assertFalse(entries[new1.block_hash].is_main_chain())
        self.assertFalse(entries[new2.block_hash].is_main_chain())
        self.assertEqual(idx.get_best_hash(), old2.block_hash)
        self.assertEqual(utxo.db.commits, 0)
        self.assertEqual(utxo.db.rollbacks, 1)

    def test_preflight_reorg_success(self):
        entries = {}
        fork = "10" * 32
        entries[fork] = _Entry(10, fork, "00" * 32)
        old1 = self._mk_chain("20", 11, 11, fork, entries)
        old2 = self._mk_chain("21", 12, 12, old1.block_hash, entries)
        new1 = self._mk_chain("30", 11, 11, fork, entries)
        new2 = self._mk_chain("31", 12, 12, new1.block_hash, entries)

        idx = _BlockIndexStub(entries)
        utxo = _UTXOStoreStub()
        mgr = ReorgManager(utxo, idx, max_reorg_depth=144)
        mgr.connect_block = _ConnectStub()
        mgr.disconnect_block = _DisconnectStub()
        idx.mark_main_chain(old1.block_hash, True)
        idx.mark_main_chain(old2.block_hash, True)
        idx.set_best_chain_tip(old2.block_hash)

        ok = mgr.can_reorganize(
            new2,
            old2,
            get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
        )
        self.assertTrue(ok)
        # Preflight must not mutate final main-chain flags.
        self.assertTrue(entries[old1.block_hash].is_main_chain())
        self.assertTrue(entries[old2.block_hash].is_main_chain())
        self.assertFalse(entries[new1.block_hash].is_main_chain())
        self.assertFalse(entries[new2.block_hash].is_main_chain())
        self.assertEqual(idx.get_best_hash(), old2.block_hash)

    def test_preflight_reorg_detects_connect_failure(self):
        entries = {}
        fork = "12" * 32
        entries[fork] = _Entry(10, fork, "00" * 32)
        old1 = self._mk_chain("22", 11, 11, fork, entries)
        old2 = self._mk_chain("23", 12, 12, old1.block_hash, entries)
        new1 = self._mk_chain("32", 11, 11, fork, entries)
        new2 = self._mk_chain("33", 12, 12, new1.block_hash, entries)

        idx = _BlockIndexStub(entries)
        utxo = _UTXOStoreStub()
        mgr = ReorgManager(utxo, idx, max_reorg_depth=144)
        mgr.connect_block = _ConnectStub(fail_on={new2.block_hash})
        mgr.disconnect_block = _DisconnectStub()
        idx.mark_main_chain(old1.block_hash, True)
        idx.mark_main_chain(old2.block_hash, True)
        idx.set_best_chain_tip(old2.block_hash)

        ok = mgr.can_reorganize(
            new2,
            old2,
            get_block_func=lambda h: _Block(h, entries[h].header.prev_block_hash.hex()),
        )
        self.assertFalse(ok)
        self.assertTrue(entries[old1.block_hash].is_main_chain())
        self.assertTrue(entries[old2.block_hash].is_main_chain())
        self.assertEqual(idx.get_best_hash(), old2.block_hash)
