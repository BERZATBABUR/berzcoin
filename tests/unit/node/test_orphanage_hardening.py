"""Unit tests for orphan handling under adversarial peers."""

import unittest

from node.p2p.orphanage import Orphanage


class _Header:
    def __init__(self, block_hash: str, parent_hash: str):
        self._block_hash = block_hash
        self.prev_block_hash = bytes.fromhex(parent_hash)

    def hash_hex(self) -> str:
        return self._block_hash


class _Block:
    def __init__(self, block_hash: str, parent_hash: str):
        self.header = _Header(block_hash, parent_hash)


class TestOrphanageHardening(unittest.TestCase):
    def test_enforces_per_peer_orphan_limit(self) -> None:
        orphanage = Orphanage(max_orphans=50, max_orphans_per_peer=2)
        parent = "00" * 32
        b1 = _Block("11" * 32, parent)
        b2 = _Block("22" * 32, parent)
        b3 = _Block("33" * 32, parent)

        self.assertTrue(orphanage.add_orphan(b1, source_peer="198.51.100.10:8333"))
        self.assertTrue(orphanage.add_orphan(b2, source_peer="198.51.100.10:8333"))
        self.assertTrue(orphanage.add_orphan(b3, source_peer="198.51.100.10:8333"))

        self.assertEqual(orphanage.size(), 2)
        self.assertFalse(orphanage.has_orphan("11" * 32))
        self.assertTrue(orphanage.has_orphan("22" * 32))
        self.assertTrue(orphanage.has_orphan("33" * 32))


if __name__ == "__main__":
    unittest.main()
