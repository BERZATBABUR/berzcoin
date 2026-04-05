"""Unit tests for persistent ban/reputation behavior."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from node.app.main import BerzCoinNode
from node.p2p.banman import BanManager
from node.p2p.peer_scoring import PeerScoringManager


class TestBanReputationPersistence(unittest.TestCase):
    def test_ban_manager_persists_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            b1 = BanManager(data_dir=datadir)
            b1.ban("198.51.100.9:8333", duration=2, reason="test")
            self.assertTrue(b1.is_banned("198.51.100.9:9999"))

            b2 = BanManager(data_dir=datadir)
            self.assertTrue(b2.is_banned("198.51.100.9:18444"))

            # Force expiration and ensure cleanup persists.
            key = b2._normalize_address("198.51.100.9:8333")
            b2.bans[key].banned_until = int(time.time()) - 1
            b2.cleanup_expired()
            self.assertFalse(b2.is_banned("198.51.100.9:8333"))

            b3 = BanManager(data_dir=datadir)
            self.assertFalse(b3.is_banned("198.51.100.9:8333"))

    def test_peer_score_bans_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            addr = "203.0.113.20:8333"

            ps1 = PeerScoringManager()
            ps1.configure_persistence(datadir)
            for _ in range(16):
                ps1.record_bad(addr, "protocol_violation")
            self.assertTrue(ps1.is_banned(addr))

            ps2 = PeerScoringManager()
            ps2.configure_persistence(datadir)
            self.assertTrue(ps2.is_banned(addr))

    def test_admin_rpc_ban_controls(self) -> None:
        async def run() -> None:
            node = BerzCoinNode()
            scoring = PeerScoringManager()
            node.connman = SimpleNamespace(peer_scores=scoring)

            banned = await node.set_ban("192.0.2.55:8333", "add", 60, "manual-test")
            self.assertEqual(banned.get("status"), "banned")

            listed = await node.list_banned()
            self.assertEqual(len(listed), 1)

            unbanned = await node.set_ban("192.0.2.55:8333", "remove", 0, "manual-test")
            self.assertEqual(unbanned.get("status"), "unbanned")

            cleared = await node.clear_banned()
            self.assertEqual(cleared.get("status"), "cleared")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
