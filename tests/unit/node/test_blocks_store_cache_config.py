"""Unit tests for block store cache sizing."""

import tempfile
import unittest
from pathlib import Path

from node.storage.blocks_store import BlocksStore
from node.storage.db import Database


class TestBlocksStoreCacheConfig(unittest.TestCase):
    def test_cache_size_respects_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = Database(data_dir, "regtest")
            db.connect()
            try:
                store = BlocksStore(db, data_dir, cache_size=256)
                self.assertEqual(store._cache_size, 256)

                tiny = BlocksStore(db, data_dir, cache_size=1)
                self.assertEqual(tiny._cache_size, 8)
            finally:
                db.disconnect()


if __name__ == "__main__":
    unittest.main()
