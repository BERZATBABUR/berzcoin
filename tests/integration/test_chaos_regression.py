"""Integration-level chaos regression check for nightly runs."""

import os

from tests.chaos.artifacts import write_json_artifact
from tests.chaos.simulator import run_chaos_simulation


def test_chaos_regression_no_divergence_under_load() -> None:
    seed = int(os.getenv("BERZ_CHAOS_INTEG_SEED", "20260406"))
    steps = int(os.getenv("BERZ_CHAOS_INTEG_STEPS", "220"))
    peer_count = int(os.getenv("BERZ_CHAOS_PEER_COUNT", "15"))

    metrics = run_chaos_simulation(seed=seed, steps=steps, peer_count=peer_count)

    assert metrics.crashes == 0
    assert not metrics.consensus_divergence
    assert metrics.peer_stats.get("total_peers", 0) > 0

    write_json_artifact(
        "chaos/integration_summary.json",
        {
            "seed": metrics.seed,
            "steps": metrics.steps,
            "peer_stats": metrics.peer_stats,
            "rejections": metrics.rejection_reasons,
            "tip_convergence_max_steps": metrics.tip_convergence_max_steps,
            "max_reorg_depth": metrics.max_reorg_depth,
            "reject_rate": metrics.reject_rate,
        },
    )
