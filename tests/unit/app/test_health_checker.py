"""Unit tests for health checker severity breakdown."""

import asyncio
import unittest

from node.app.health import HealthChecker


class _DB:
    def execute(self, _q):
        return 1

    def check_consistency(self, quick=True):
        return {"integrity_ok": True, "foreign_keys_ok": True, "mode": "quick_check"}


class _Chain:
    def get_best_height(self):
        return 123


class _Conn:
    def get_connected_count(self):
        return 0

    def get_best_height_peer(self):
        return None


class _Peer:
    def __init__(self, h):
        self.peer_height = h


class _ConnLag:
    def get_connected_count(self):
        return 2

    def get_best_height_peer(self):
        return _Peer(250)


class _Mode:
    def is_full_node(self):
        return True


class _Config:
    def get(self, key, default=None):
        values = {
            "health_sync_lag_warn_blocks": 24,
            "health_sync_lag_critical_blocks": 120,
            "health_min_peers_warn": 1,
            "health_max_mempool_txs_warn": 200000,
        }
        return values.get(key, default)


class _Node:
    def __init__(self):
        self.db = _DB()
        self.chainstate = _Chain()
        self.connman = _Conn()
        self.mempool = None
        self.wallet = None
        self.mode_manager = _Mode()
        self.config = _Config()


class TestHealthChecker(unittest.TestCase):
    def test_check_includes_summary_and_degraded_status(self) -> None:
        async def run() -> None:
            checker = HealthChecker(_Node())
            result = await checker.check()
            self.assertIn("summary", result)
            self.assertIn("warning_count", result["summary"])
            self.assertEqual(result.get("status"), "degraded")
            self.assertIn("consistency", result["checks"]["database"])

        asyncio.run(run())

    def test_sync_lag_can_mark_node_unhealthy(self) -> None:
        async def run() -> None:
            node = _Node()
            node.connman = _ConnLag()
            checker = HealthChecker(node)
            result = await checker.check()
            self.assertEqual(result["checks"]["sync"]["status"], "unhealthy")
            self.assertEqual(result["checks"]["sync"]["lag"], 127)
            self.assertEqual(result["status"], "unhealthy")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
