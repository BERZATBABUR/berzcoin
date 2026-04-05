"""Chaos test suite: partitions, reconnect storms, inv spam, stale churn, reorg load."""

import os
import unittest

from tests.chaos.artifacts import write_json_artifact, write_jsonl_artifact
from tests.chaos.mempool_simulator import run_mempool_chaos_simulation
from tests.chaos.simulator import run_chaos_simulation


class TestNetworkChaosSuite(unittest.TestCase):
    def test_chaos_smoke_stability(self) -> None:
        seed = int(os.getenv("BERZ_CHAOS_SEED", "20260405"))
        steps = int(os.getenv("BERZ_CHAOS_STEPS", "320"))
        peer_count = int(os.getenv("BERZ_CHAOS_PEER_COUNT", "15"))

        metrics = run_chaos_simulation(seed=seed, steps=steps, peer_count=peer_count)

        self.assertEqual(metrics.crashes, 0)
        self.assertFalse(metrics.consensus_divergence)
        self.assertGreater(len(metrics.mempool_growth), 0)
        self.assertGreaterEqual(metrics.peer_stats.get("active_peers", 0), 4)

        peak_mempool = max(row["size"] for row in metrics.mempool_growth)
        self.assertLess(peak_mempool, 250_000)
        self.assertLessEqual(metrics.tip_convergence_max_steps, 120)
        self.assertLessEqual(metrics.max_reorg_depth, 16)
        self.assertLess(metrics.reject_rate, 1.0)

        write_json_artifact("chaos/peer_stats.json", metrics.peer_stats)
        write_json_artifact("chaos/rejection_reasons.json", metrics.rejection_reasons)
        write_json_artifact(
            "chaos/network_summary.json",
            {
                "seed": metrics.seed,
                "steps": metrics.steps,
                "tip_convergence_max_steps": metrics.tip_convergence_max_steps,
                "max_reorg_depth": metrics.max_reorg_depth,
                "reject_rate": metrics.reject_rate,
                "crashes": metrics.crashes,
                "consensus_divergence": metrics.consensus_divergence,
                "active_peers": metrics.peer_stats.get("active_peers", 0),
            },
        )
        write_jsonl_artifact("chaos/mempool_growth.jsonl", metrics.mempool_growth)

    def test_mempool_chaos_smoke_stability(self) -> None:
        seed = int(os.getenv("BERZ_MEMPOOL_CHAOS_SEED", "20260407"))
        steps = int(os.getenv("BERZ_MEMPOOL_CHAOS_STEPS", "240"))

        metrics = run_mempool_chaos_simulation(seed=seed, steps=steps)

        self.assertEqual(metrics.crashes, 0)
        self.assertFalse(metrics.consensus_drift)
        self.assertGreater(len(metrics.mempool_growth), 0)
        self.assertGreaterEqual(metrics.peak_mempool_size, 1)
        self.assertLess(metrics.peak_mempool_size, 181)  # bounded by simulator limits
        self.assertLess(metrics.peak_mempool_vsize, 300_000)

        write_json_artifact(
            "chaos/mempool_summary.json",
            {
                "seed": metrics.seed,
                "steps": metrics.steps,
                "crashes": metrics.crashes,
                "consensus_drift": metrics.consensus_drift,
                "peak_mempool_size": metrics.peak_mempool_size,
                "peak_mempool_vsize": metrics.peak_mempool_vsize,
            },
        )
        write_json_artifact("chaos/mempool_reject_reasons.json", metrics.reject_reasons)
        write_json_artifact("chaos/mempool_eviction_reasons.json", metrics.eviction_reasons)
        write_jsonl_artifact("chaos/mempool_size_growth.jsonl", metrics.mempool_growth)

    @unittest.skipUnless(
        os.getenv("BERZ_CHAOS_LONG", "0") == "1",
        "set BERZ_CHAOS_LONG=1 for long-run chaos",
    )
    def test_chaos_long_run(self) -> None:
        seed = int(os.getenv("BERZ_CHAOS_SEED", "20260405"))
        steps = int(os.getenv("BERZ_CHAOS_LONG_STEPS", "4000"))
        peer_count = int(os.getenv("BERZ_CHAOS_PEER_COUNT", "15"))

        metrics = run_chaos_simulation(seed=seed, steps=steps, peer_count=peer_count)

        self.assertEqual(metrics.crashes, 0)
        self.assertFalse(metrics.consensus_divergence)
        self.assertGreater(metrics.peer_stats.get("total_peers", 0), 0)

        write_json_artifact(
            "chaos/long_run_summary.json",
            {
                "seed": metrics.seed,
                "steps": metrics.steps,
                "peer_stats": metrics.peer_stats,
                "rejection_reasons": metrics.rejection_reasons,
                "crashes": metrics.crashes,
                "tip_convergence_max_steps": metrics.tip_convergence_max_steps,
                "max_reorg_depth": metrics.max_reorg_depth,
                "reject_rate": metrics.reject_rate,
            },
        )


if __name__ == "__main__":
    unittest.main()
