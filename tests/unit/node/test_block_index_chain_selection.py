"""Unit tests for block-index chainwork and main-chain selection behavior."""

import unittest

from node.chain.block_index import BlockIndex


class _DBStub:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetch_all(self, _query, _params=()):
        return list(self._rows)


class _HeaderStub:
    def __init__(self, block_hash: str, prev_hash: str):
        self._block_hash = block_hash
        self.prev_block_hash = bytes.fromhex(prev_hash)

    def hash_hex(self):
        return self._block_hash


class _BlockStub:
    def __init__(self, block_hash: str, prev_hash: str):
        self.header = _HeaderStub(block_hash, prev_hash)


class TestBlockIndexChainSelection(unittest.TestCase):
    def test_load_prefers_best_chainwork_over_height(self):
        # Higher height but less work should not become best tip.
        rows = [
            {
                "hash": "11" * 32,
                "height": 10,
                "version": 1,
                "prev_block_hash": "00" * 32,
                "merkle_root": "22" * 32,
                "timestamp": 1,
                "bits": 0x207FFFFF,
                "nonce": 0,
                "chainwork": "1000",
                "is_valid": True,
            },
            {
                "hash": "33" * 32,
                "height": 11,
                "version": 1,
                "prev_block_hash": "11" * 32,
                "merkle_root": "44" * 32,
                "timestamp": 2,
                "bits": 0x207FFFFF,
                "nonce": 0,
                "chainwork": "900",
                "is_valid": True,
            },
        ]
        idx = BlockIndex(_DBStub(rows))
        idx.load()
        self.assertEqual(idx.get_best_hash(), "11" * 32)
        self.assertEqual(idx.get_best_height(), 10)

    def test_height_lookup_tracks_only_selected_main_chain(self):
        idx = BlockIndex(_DBStub())
        genesis = "aa" * 32
        a1 = "ab" * 32
        a2 = "ac" * 32
        b2 = "bc" * 32

        idx.add_block(_BlockStub(genesis, "00" * 32), height=0, chainwork=100)
        idx.add_block(_BlockStub(a1, genesis), height=1, chainwork=200)
        idx.add_block(_BlockStub(a2, a1), height=2, chainwork=300)

        # Competing fork at same height but lower total work.
        idx.add_block(_BlockStub(b2, a1), height=2, chainwork=250)

        self.assertEqual(idx.get_best_hash(), a2)
        self.assertEqual(idx.get_block_by_height(2).block_hash, a2)

        # If fork becomes heavier later, switching best tip should update height map.
        idx.add_block(_BlockStub(b2, a1), height=2, chainwork=350)
        self.assertEqual(idx.get_best_hash(), b2)
        self.assertEqual(idx.get_block_by_height(2).block_hash, b2)


if __name__ == "__main__":
    unittest.main()
