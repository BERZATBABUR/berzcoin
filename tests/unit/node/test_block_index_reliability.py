"""Block index reliability and consistency tests."""

import tempfile
import unittest
from pathlib import Path

from node.chain.block_index import BlockIndex, BlockStatus
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations


class _HeaderStub:
    def __init__(self, block_hash: str, prev_hash: str):
        self._block_hash = block_hash
        self.prev_block_hash = bytes.fromhex(prev_hash)

    def hash_hex(self):
        return self._block_hash


class _BlockStub:
    def __init__(self, block_hash: str, prev_hash: str):
        self.header = _HeaderStub(block_hash, prev_hash)


class _BlocksStoreStub:
    def __init__(self, missing_hashes=None):
        self._missing = set(missing_hashes or [])

    def read_block_by_hash(self, block_hash: str):
        if block_hash in self._missing:
            return None
        return object()


class TestBlockIndexReliability(unittest.TestCase):
    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        datadir = Path(tmp.name)
        db = Database(datadir, "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return tmp, db

    def test_status_and_chainwork_selection_and_lookup(self):
        tmp, db = self._db()
        try:
            idx = BlockIndex(db)
            g = "aa" * 32
            a1 = "ab" * 32
            a2 = "ac" * 32
            b2 = "bc" * 32
            idx.add_block(_BlockStub(g, "00" * 32), 0, 100)
            idx.add_block(_BlockStub(a1, g), 1, 200)
            idx.add_block(_BlockStub(a2, a1), 2, 300)
            idx.add_block(_BlockStub(b2, a1), 2, 250)
            self.assertEqual(idx.get_block_by_height(2).block_hash, a2)
            self.assertTrue(idx.get_block(a2).has_status(BlockStatus.MAIN_CHAIN))
            self.assertTrue(idx.get_block(b2).has_status(BlockStatus.SIDE_CHAIN))
            self.assertEqual(idx.get_best_hash(), a2)
            self.assertEqual(idx.get_best_height(), 2)

            idx.add_block(_BlockStub(b2, a1), 2, 350)
            self.assertEqual(idx.get_best_hash(), b2)
            self.assertEqual(idx.get_block_by_height(2).block_hash, b2)

            fork, tip = idx.find_fork(b2)
            self.assertEqual(fork.block_hash, b2)
            self.assertEqual(tip.block_hash, b2)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_invalid_branch_is_remembered_and_not_promoted(self):
        tmp, db = self._db()
        try:
            idx = BlockIndex(db)
            g = "11" * 32
            c1 = "12" * 32
            c2 = "13" * 32
            idx.add_block(_BlockStub(g, "00" * 32), 0, 100)
            idx.add_block(_BlockStub(c1, g), 1, 200)
            idx.mark_invalid(c1, "bad-pow")
            idx.add_block(_BlockStub(c2, c1), 2, 500)
            self.assertTrue(idx.is_known_invalid(c1))
            self.assertTrue(idx.is_branch_invalid(c2))
            self.assertEqual(idx.get_invalid_reason(c1), "bad-pow")
            # Higher-work child of invalid parent must not become best.
            self.assertEqual(idx.get_best_hash(), g)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_persistence_after_restart(self):
        tmp, db = self._db()
        try:
            idx = BlockIndex(db)
            g = "21" * 32
            b1 = "22" * 32
            db.execute(
                """
                INSERT INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid, status_flags)
                VALUES (?, 0, 1, ?, ?, 1, ?, 0, ?, 1, ?)
                """,
                (g, "00" * 32, "33" * 32, 0x207FFFFF, "100", int(BlockStatus.HEADER | BlockStatus.VALID)),
            )
            db.execute(
                """
                INSERT INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid, status_flags)
                VALUES (?, 1, 1, ?, ?, 2, ?, 0, ?, 1, ?)
                """,
                (b1, g, "44" * 32, 0x207FFFFF, "200", int(BlockStatus.HEADER | BlockStatus.VALID)),
            )
            idx.load()
            self.assertEqual(idx.get_best_hash(), b1)
            self.assertEqual(idx.get_best_height(), 1)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_consistency_checks_cover_partial_cases(self):
        tmp, db = self._db()
        try:
            idx = BlockIndex(db)
            g = "31" * 32
            o = "32" * 32
            idx.add_block(_BlockStub(g, "00" * 32), 0, 10)
            idx.add_block(_BlockStub(o, "ff" * 32), 1, 20, update_best=False)
            idx.get_block(o).raw_meta["file_path"] = "blocks/32.blk"
            idx._best_hash = "ee" * 32  # deliberate inconsistency for checker
            idx._height_index[0] = "00" * 32  # deliberate mismatch
            report = idx.validate_consistency(blocks_store=_BlocksStoreStub(missing_hashes={o}))
            issues = set(report["issues"])
            self.assertTrue(any(i.startswith("best_tip_missing_from_index") for i in issues))
            self.assertTrue(any(i.startswith("missing_parent:") for i in issues))
            self.assertTrue(any(i.startswith("height_index_mismatch:") for i in issues))
            self.assertTrue(any(i.startswith("missing_or_corrupt_raw_block:") for i in issues))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_corrupted_chainwork_is_detected_on_load(self):
        tmp, db = self._db()
        try:
            bad = "41" * 32
            db.execute(
                """
                INSERT INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid, status_flags)
                VALUES (?, 1, 1, ?, ?, 1, ?, 0, ?, 1, ?)
                """,
                (bad, "00" * 32, "55" * 32, 0x207FFFFF, "not-a-number", int(BlockStatus.HEADER | BlockStatus.VALID)),
            )
            idx = BlockIndex(db)
            idx.load()
            self.assertTrue(idx.is_known_invalid(bad))
            report = idx.validate_consistency()
            self.assertFalse(report["ok"])
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
