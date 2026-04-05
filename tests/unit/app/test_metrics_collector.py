"""Unit tests for node metrics collector outputs."""

import unittest

from node.app.metrics import MetricsCollector


class _Cfg:
    def get(self, key, default=None):
        if key == "health_sync_lag_warn_blocks":
            return 24
        return default


class _DB:
    def get_size(self):
        return 4096


class _UTXO:
    def get_utxo_count(self):
        return 12

    def get_total_value(self):
        return 3456


class _Chain:
    def get_best_height(self):
        return 100


class _Peer:
    peer_height = 110


class _Conn:
    def get_connected_count(self):
        return 3

    def get_best_height_peer(self):
        return _Peer()


class _Sync:
    def __init__(self):
        self._pending_block_requests = {"a": 1.0, "b": 2.0}

        class _Orph:
            @staticmethod
            def size():
                return 4

        self.orphanage = _Orph()


class _Mempool:
    def __init__(self):
        self.transactions = {"a": object(), "b": object()}
        self.total_weight = 777
        self.min_fee_floor_rate = 2.5
        self.reject_reason_counts = {"fee_too_low": 3}
        self.eviction_reason_counts = {"mempool_space": 1}


class _Health:
    def is_ready(self):
        return True


class _Node:
    def __init__(self):
        self.config = _Cfg()
        self.db = _DB()
        self.utxo_store = _UTXO()
        self.chainstate = _Chain()
        self.connman = _Conn()
        self.block_sync = _Sync()
        self.mempool = _Mempool()
        self.health_checker = _Health()


class TestMetricsCollector(unittest.TestCase):
    def test_metrics_include_chainstate_sync_and_slo(self) -> None:
        collector = MetricsCollector(_Node())
        metrics = collector.get_metrics()

        self.assertEqual(metrics["node"]["sync_lag_blocks"], 10)
        self.assertEqual(metrics["node"]["pending_block_requests"], 2)
        self.assertEqual(metrics["node"]["orphan_blocks"], 4)
        self.assertEqual(metrics["chainstate"]["db_size_bytes"], 4096)
        self.assertEqual(metrics["chainstate"]["utxo_count"], 12)
        self.assertEqual(metrics["chainstate"]["utxo_total_value"], 3456)
        self.assertTrue(metrics["slo"]["ready"])
        self.assertTrue(metrics["slo"]["sync_lag_slo_ok"])
        self.assertEqual(metrics["node"]["mempool_min_fee_floor_rate"], 2.5)
        self.assertEqual(metrics["mempool_policy"]["reject_reason_counts"]["fee_too_low"], 3)
        self.assertEqual(metrics["mempool_policy"]["eviction_reason_counts"]["mempool_space"], 1)

    def test_prometheus_export_contains_new_series(self) -> None:
        collector = MetricsCollector(_Node())
        body = collector.to_prometheus()

        self.assertIn("berzcoin_sync_lag_blocks", body)
        self.assertIn("berzcoin_chainstate_db_size_bytes", body)
        self.assertIn("berzcoin_utxo_count", body)
        self.assertIn("berzcoin_readiness_slo", body)
        self.assertIn("berzcoin_mempool_min_fee_floor_rate", body)
        self.assertIn("berzcoin_mempool_reject_reason_total", body)
        self.assertIn("berzcoin_mempool_eviction_reason_total", body)


if __name__ == "__main__":
    unittest.main()
