"""Validate RC soak artifacts and enforce pass/fail gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


MANDATORY_REL_PATHS = [
    "junit.xml",
    "artifacts/chaos/peer_stats.json",
    "artifacts/chaos/rejection_reasons.json",
    "artifacts/chaos/mempool_growth.jsonl",
    "artifacts/chaos/mempool_summary.json",
    "artifacts/chaos/mempool_reject_reasons.json",
    "artifacts/chaos/mempool_eviction_reasons.json",
    "artifacts/chaos/mempool_size_growth.jsonl",
    "artifacts/chaos/long_run_summary.json",
]


def _load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_dirs(run_dir: Path) -> List[Path]:
    out = []
    for p in sorted(run_dir.glob("iter-*")):
        if p.is_dir():
            out.append(p)
    return out


def validate_run(run_dir: Path, max_mempool_vsize: int) -> Dict:
    iterations = _iter_dirs(run_dir)
    report: Dict[str, object] = {
        "run_dir": str(run_dir),
        "iterations": [],
        "pass": True,
        "reasons": [],
        "max_mempool_vsize_bound": int(max_mempool_vsize),
    }

    if not iterations:
        report["pass"] = False
        report["reasons"].append("no_iterations_found")
        return report

    for it in iterations:
        iteration_report: Dict[str, object] = {"iter_dir": it.name, "pass": True, "missing": [], "checks": {}}
        for rel in MANDATORY_REL_PATHS:
            if not (it / rel).exists():
                iteration_report["missing"].append(rel)
        if iteration_report["missing"]:
            iteration_report["pass"] = False

        try:
            mempool_summary = _load_json(it / "artifacts/chaos/mempool_summary.json")
            peak_vsize = int(mempool_summary.get("peak_mempool_vsize", 0))
            crashes = int(mempool_summary.get("crashes", 0))
            drift = bool(mempool_summary.get("consensus_drift", True))
            iteration_report["checks"]["mempool_peak_vsize"] = peak_vsize
            iteration_report["checks"]["mempool_crashes"] = crashes
            iteration_report["checks"]["consensus_drift"] = drift
            if peak_vsize > int(max_mempool_vsize):
                iteration_report["pass"] = False
                iteration_report["checks"]["mempool_bound_ok"] = False
            else:
                iteration_report["checks"]["mempool_bound_ok"] = True
            if crashes != 0 or drift:
                iteration_report["pass"] = False
        except Exception as exc:
            iteration_report["pass"] = False
            iteration_report["checks"]["mempool_summary_error"] = str(exc)

        try:
            long_summary = _load_json(it / "artifacts/chaos/long_run_summary.json")
            long_crashes = int(long_summary.get("crashes", 1))
            iteration_report["checks"]["long_run_crashes"] = long_crashes
            if long_crashes != 0:
                iteration_report["pass"] = False
        except Exception as exc:
            iteration_report["pass"] = False
            iteration_report["checks"]["long_run_summary_error"] = str(exc)

        report["iterations"].append(iteration_report)
        if not iteration_report["pass"]:
            report["pass"] = False

    if not report["pass"]:
        report["reasons"].append("one_or_more_iterations_failed_validation")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate release soak artifacts")
    parser.add_argument("--run-dir", required=True, help="Soak run directory (contains iter-*)")
    parser.add_argument("--max-mempool-vsize", type=int, default=300000, help="Memory bound gate")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    report = validate_run(run_dir, max_mempool_vsize=int(args.max_mempool_vsize))

    out = run_dir / "soak_validation.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(str(out))
    print(json.dumps({"pass": bool(report.get("pass")), "reasons": report.get("reasons", [])}))

    return 0 if bool(report.get("pass")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

