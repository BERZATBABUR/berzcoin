"""Assert that latest stage runs hold SLOs for the required week window."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z-to-\d{8}T\d{6}Z$")


def _run_dirs(stage_dir: Path) -> List[Tuple[str, Path]]:
    runs = []
    for p in stage_dir.iterdir():
        if p.is_dir() and RUN_DIR_RE.match(p.name):
            runs.append((p.name, p))
    runs.sort(key=lambda row: row[0])
    return runs


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Assert recent field-validation run window")
    parser.add_argument("--artifact-base", required=True, help="Base field-validation artifact directory")
    parser.add_argument("--stage", required=True, choices=["A", "B", "C"])
    parser.add_argument("--min-runs", type=int, default=2, help="Minimum latest runs required to pass")
    parser.add_argument("--max-runs", type=int, default=4, help="Maximum latest runs to inspect")
    args = parser.parse_args()

    if args.min_runs < 2 or args.max_runs < args.min_runs:
        print(json.dumps({"pass": False, "reason": "invalid_window_bounds"}))
        return 1

    stage_dir = Path(args.artifact_base).resolve() / args.stage
    if not stage_dir.exists():
        print(json.dumps({"pass": False, "reason": "missing_stage_dir", "stage_dir": str(stage_dir)}))
        return 1

    runs = _run_dirs(stage_dir)
    if len(runs) < args.min_runs:
        print(
            json.dumps(
                {
                    "pass": False,
                    "reason": "insufficient_runs",
                    "required": args.min_runs,
                    "found": len(runs),
                }
            )
        )
        return 1

    target_count = min(len(runs), int(args.max_runs))
    recent = runs[-target_count:]
    checks = []
    failures = []
    for run_name, run_dir in recent:
        path = run_dir / "field_validation_slo.json"
        if not path.exists():
            checks.append({"run": run_name, "pass": False, "reason": "missing_field_validation_slo_json"})
            failures.append(run_name)
            continue
        payload = _load(path)
        run_pass = bool(payload.get("pass"))
        checks.append({"run": run_name, "pass": run_pass})
        if not run_pass:
            failures.append(run_name)

    result = {
        "pass": len(failures) == 0 and len(recent) >= args.min_runs,
        "stage": args.stage,
        "window_checked": len(recent),
        "min_required": int(args.min_runs),
        "max_checked": int(args.max_runs),
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
