"""Unit tests for DB consistency checks used in crash recovery health paths."""

import tempfile
import unittest
from pathlib import Path

from node.storage.db import Database


class TestDBConsistency(unittest.TestCase):
    def test_check_consistency_reports_clean_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir), "regtest")
            db.connect()
            try:
                db.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
                db.execute("INSERT INTO t (v) VALUES (?)", ("ok",))
                result = db.check_consistency(quick=True)
                self.assertTrue(result.get("integrity_ok"))
                self.assertTrue(result.get("foreign_keys_ok"))
            finally:
                db.disconnect()


if __name__ == "__main__":
    unittest.main()
