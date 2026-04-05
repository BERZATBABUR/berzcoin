"""Integration-style reorg tests across consensus activation boundaries."""

import unittest
from contextlib import contextmanager

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
    def __init__(self, block_hash: str, prev_hash: str, height: int, tx_version: int):
        self.header = _Header(block_hash, prev_hash)
        self.height = int(height)
        self.tx_version = int(tx_version)


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


class _ActivationAwareConnect:
    """Rejects old tx version at or after activation height."""

    def __init__(self, activation_height: int):
        self.activation_height = int(activation_height)

    def connect(self, block):
        if int(block.height) >= self.activation_height and int(block.tx_version) < 2:
            return False
        return True


class _Disconnect:
    def disconnect(self, _block):
        return True


def _mk_chain(prefix: str, start: int, end: int, parent_hash: str, entries):
    prev = parent_hash
    for h in range(start, end + 1):
        bh = (prefix + f"{h:06d}")[:64].ljust(64, "0")
        entries[bh] = _Entry(h, bh, prev)
        prev = bh
    return entries[prev]


class TestReorgActivationBoundary(unittest.TestCase):
    def _mark_active_branch(self, idx: _BlockIndex, tip: _Entry):
        cur = tip
        while cur is not None:
            idx.mark_main_chain(cur.block_hash, True)
            cur = idx.get_block(cur.header.prev_block_hash.hex())

    def test_reorg_across_activation_fails_on_old_rule_blocks(self):
        entries = {}
        blocks = {}
        activation_height = 52

        fork = "aa" * 32
        entries[fork] = _Entry(49, fork, "00" * 32)
        blocks[fork] = _Block(fork, "00" * 32, 49, tx_version=1)

        old_tip = _mk_chain("bb", 50, 52, fork, entries)
        new_tip = _mk_chain("cc", 50, 53, fork, entries)

        for h in range(50, 53):
            bh = ("bb" + f"{h:06d}")[:64].ljust(64, "0")
            prev = fork if h == 50 else ("bb" + f"{h-1:06d}")[:64].ljust(64, "0")
            blocks[bh] = _Block(bh, prev, h, tx_version=1)
        for h in range(50, 54):
            bh = ("cc" + f"{h:06d}")[:64].ljust(64, "0")
            prev = fork if h == 50 else ("cc" + f"{h-1:06d}")[:64].ljust(64, "0")
            blocks[bh] = _Block(bh, prev, h, tx_version=1)

        idx = _BlockIndex(entries)
        utxo = _UTXOStore()
        mgr = ReorgManager(utxo, idx, max_reorg_depth=144)
        mgr.connect_block = _ActivationAwareConnect(activation_height=activation_height)
        mgr.disconnect_block = _Disconnect()

        self._mark_active_branch(idx, old_tip)
        idx.set_best_chain_tip(old_tip.block_hash)

        ok = mgr.can_reorganize(new_tip, old_tip, get_block_func=lambda h: blocks[h])
        self.assertFalse(ok)

        ok, disconnected, connected = mgr.reorganize(
            new_tip,
            old_tip,
            get_block_func=lambda h: blocks[h],
        )
        self.assertFalse(ok)
        self.assertEqual(disconnected, [])
        self.assertEqual(connected, [])
        self.assertTrue(entries[old_tip.block_hash].is_main_chain())
        self.assertFalse(entries[new_tip.block_hash].is_main_chain())
        self.assertEqual(idx.get_best_hash(), old_tip.block_hash)
        self.assertEqual(utxo.db.commits, 0)
        self.assertEqual(utxo.db.rollbacks, 1)

    def test_reorg_across_activation_succeeds_with_upgraded_blocks(self):
        entries = {}
        blocks = {}
        activation_height = 52

        fork = "dd" * 32
        entries[fork] = _Entry(49, fork, "00" * 32)
        blocks[fork] = _Block(fork, "00" * 32, 49, tx_version=1)

        old_tip = _mk_chain("ee", 50, 52, fork, entries)
        new_tip = _mk_chain("ff", 50, 53, fork, entries)

        for h in range(50, 53):
            bh = ("ee" + f"{h:06d}")[:64].ljust(64, "0")
            prev = fork if h == 50 else ("ee" + f"{h-1:06d}")[:64].ljust(64, "0")
            blocks[bh] = _Block(bh, prev, h, tx_version=1)
        for h in range(50, 54):
            bh = ("ff" + f"{h:06d}")[:64].ljust(64, "0")
            prev = fork if h == 50 else ("ff" + f"{h-1:06d}")[:64].ljust(64, "0")
            version = 2 if h >= activation_height else 1
            blocks[bh] = _Block(bh, prev, h, tx_version=version)

        idx = _BlockIndex(entries)
        utxo = _UTXOStore()
        mgr = ReorgManager(utxo, idx, max_reorg_depth=144)
        mgr.connect_block = _ActivationAwareConnect(activation_height=activation_height)
        mgr.disconnect_block = _Disconnect()

        self._mark_active_branch(idx, old_tip)
        idx.set_best_chain_tip(old_tip.block_hash)

        ok = mgr.can_reorganize(new_tip, old_tip, get_block_func=lambda h: blocks[h])
        self.assertTrue(ok)

        ok, disconnected, connected = mgr.reorganize(
            new_tip,
            old_tip,
            get_block_func=lambda h: blocks[h],
        )
        self.assertTrue(ok)
        self.assertEqual(len(disconnected), 3)
        self.assertEqual(len(connected), 4)
        self.assertFalse(entries[old_tip.block_hash].is_main_chain())
        self.assertTrue(entries[new_tip.block_hash].is_main_chain())
        self.assertEqual(idx.get_best_hash(), new_tip.block_hash)
        self.assertEqual(utxo.db.commits, 1)
        self.assertEqual(utxo.db.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
