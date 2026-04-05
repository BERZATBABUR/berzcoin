"""Unit tests for mempool observability RPC surfaces."""

import asyncio
import unittest

from node.rpc.handlers.mempool import MempoolHandlers


class _Policy:
    min_relay_fee = 3


class _Limits:
    max_size = 123456


class _Mempool:
    def __init__(self):
        self.policy = _Policy()
        self.limits = _Limits()
        self.last_reject_reason = "fee_too_low"

    async def get_stats(self):
        return {
            "size": 5,
            "total_size": 2048,
            "total_vsize": 512,
            "total_weight": 4096,
            "total_fee": 10000,
            "policy": {
                "min_fee_floor_rate": 4.0,
                "reject_reason_counts": {"fee_too_low": 7, "rbf_policy": 2},
                "eviction_reason_counts": {"mempool_space": 3},
            },
        }

    def get_policy_thresholds(self):
        return {
            "policy": {"min_relay_fee": 3},
            "limits": {"max_transactions": 5000},
            "rolling_fee_floor_rate": 4.0,
            "rolling_fee_floor_half_life_secs": 600.0,
            "last_reject_reason": "fee_too_low",
        }

    def get_eviction_snapshot(self, limit: int = 10):
        return {
            "candidate_count": min(2, int(limit)),
            "candidates": [{"txid": "ab" * 32}, {"txid": "cd" * 32}][: max(1, int(limit))],
            "totals": {"txs": 5, "size_bytes": 2048, "vsize": 512, "weight": 4096},
        }


class _Node:
    def __init__(self):
        self.mempool = _Mempool()


class TestMempoolObservabilityRPC(unittest.TestCase):
    def test_get_mempool_info_includes_thresholds_and_eviction_snapshot(self) -> None:
        async def run() -> None:
            handlers = MempoolHandlers(_Node())
            info = await handlers.get_mempool_info()
            self.assertEqual(info.get("loaded"), True)
            self.assertIn("policy_thresholds", info)
            self.assertIn("eviction_snapshot", info)
            self.assertEqual(info.get("maxmempool"), 123456)

        asyncio.run(run())

    def test_get_mempool_diagnostics_returns_detailed_histograms(self) -> None:
        async def run() -> None:
            handlers = MempoolHandlers(_Node())
            diag = await handlers.get_mempool_diagnostics(top_n=1)
            self.assertEqual(diag.get("size"), 5)
            self.assertEqual(diag.get("last_reject_reason"), "fee_too_low")
            self.assertIn("policy_thresholds", diag)
            self.assertIn("eviction_snapshot", diag)
            self.assertEqual(len(diag.get("reject_reasons_top", [])), 1)
            self.assertEqual(len(diag.get("eviction_reasons_top", [])), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

