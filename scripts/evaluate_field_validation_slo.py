"""Evaluate Stage A/B/C field-validation artifacts against SLO gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_profiles(path: Path) -> Dict[str, Any]:
    cfg = _load_json(path)
    stages = cfg.get("stages", {})
    if not isinstance(stages, dict):
        raise ValueError("invalid profile config: missing 'stages'")
    return stages


def evaluate_run(run_dir: Path, profile: Dict[str, Any]) -> Dict[str, Any]:
    network_summary = _load_json(run_dir / "artifacts/chaos/network_summary.json")
    mempool_summary = _load_json(run_dir / "artifacts/chaos/mempool_summary.json")
    long_run_summary = _load_json(run_dir / "artifacts/chaos/long_run_summary.json")
    fault_summary = _load_json(run_dir / "artifacts/chaos/fault_injection_soak_summary.json")

    slo = profile.get("slo", {})
    tip_bound = int(slo.get("tip_convergence_max_steps", 45))
    reorg_bound = int(slo.get("max_reorg_depth", 12))
    mempool_bound = int(slo.get("mempool_peak_vsize_max", 300000))
    reject_rate_bound = float(slo.get("reject_rate_max", 0.40))

    tip_value = int(network_summary.get("tip_convergence_max_steps", 0))
    reorg_value = int(network_summary.get("max_reorg_depth", 0))
    mempool_vsize = int(mempool_summary.get("peak_mempool_vsize", 0))
    reject_rate = float(network_summary.get("reject_rate", 1.0))
    crashes = int(mempool_summary.get("crashes", 1)) + int(long_run_summary.get("crashes", 1))
    consensus_drift = bool(mempool_summary.get("consensus_drift", True))
    consensus_divergence = bool(network_summary.get("consensus_divergence", True))
    failed_reorgs = int(fault_summary.get("failed_reorgs", 0))

    checks = {
        "tip_convergence_max_steps": {"value": tip_value, "bound": tip_bound, "pass": tip_value <= tip_bound},
        "max_reorg_depth": {"value": reorg_value, "bound": reorg_bound, "pass": reorg_value <= reorg_bound},
        "mempool_peak_vsize": {
            "value": mempool_vsize,
            "bound": mempool_bound,
            "pass": mempool_vsize <= mempool_bound,
        },
        "reject_rate": {"value": reject_rate, "bound": reject_rate_bound, "pass": reject_rate <= reject_rate_bound},
        "combined_crashes": {"value": crashes, "bound": 0, "pass": crashes == 0},
        "consensus_drift": {"value": consensus_drift, "bound": False, "pass": consensus_drift is False},
        "consensus_divergence": {
            "value": consensus_divergence,
            "bound": False,
            "pass": consensus_divergence is False,
        },
        "fault_injection_failed_reorgs": {
            "value": failed_reorgs,
            "bound": "informational",
            "pass": True,
        },
    }

    passed = all(bool(v.get("pass")) for v in checks.values())
    failures = [name for name, payload in checks.items() if not bool(payload.get("pass"))]
    return {
        "run_dir": str(run_dir),
        "pass": passed,
        "checks": checks,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate field-validation SLO run")
    parser.add_argument("--run-dir", required=True, help="Run directory containing artifacts/")
    parser.add_argument("--stage", required=True, choices=["A", "B", "C"])
    parser.add_argument(
        "--profiles",
        default="configs/field_validation_profiles.json",
        help="Path to stage profile JSON",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    profiles = _load_profiles(Path(args.profiles))
    profile = profiles.get(args.stage)
    if not isinstance(profile, dict):
        raise SystemExit(f"missing profile for stage {args.stage}")

    result = evaluate_run(run_dir=run_dir, profile=profile)
    out = run_dir / "field_validation_slo.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(str(out))
    print(json.dumps({"pass": result["pass"], "failures": result["failures"]}))
    return 0 if bool(result.get("pass")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
