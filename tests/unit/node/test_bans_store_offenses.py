"""Regression tests for persistent offense tracking in bans store."""

import tempfile
import unittest
from pathlib import Path

from node.storage.bans_store import BansStore
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations


class TestBansStoreOffenses(unittest.TestCase):
    def _setup_db(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name), "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return tmp, db

    def test_record_offense_increments_and_bans_on_threshold(self) -> None:
        tmp, db = self._setup_db()
        try:
            store = BansStore(db)
            addr = "198.51.100.8:8333"

            store.record_offense(addr, "bad-msg")
            store.record_offense(addr, "bad-msg")
            self.assertFalse(store.is_banned(addr))

            offense_row = db.fetch_one(
                "SELECT value FROM settings WHERE key = ?",
                ("offense_count:198.51.100.8:8333",),
            )
            self.assertIsNotNone(offense_row)
            self.assertEqual(int(offense_row["value"]), 2)

            store.record_offense(addr, "bad-msg")
            self.assertTrue(store.is_banned(addr))
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
