"""Assert that the latest N soak runs for an RC tag all passed validation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Tuple


RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z-to-\d{8}T\d{6}Z$")


def _is_run_dir(path: Path) -> bool:
    return path.is_dir() and RUN_DIR_RE.match(path.name) is not None


def _load_validation(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_runs(rc_dir: Path) -> List[Tuple[str, Path]]:
    runs = []
    for entry in rc_dir.iterdir():
        if _is_run_dir(entry):
            runs.append((entry.name, entry))
    runs.sort(key=lambda pair: pair[0])
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that the latest N RC soak runs all passed validation."
    )
    parser.add_argument("--artifact-base", required=True, help="Base RC soak artifact dir")
    parser.add_argument("--rc-tag", required=True, help="Release candidate tag, e.g. v2.0.0-rc1")
    parser.add_argument(
        "--required-passes",
        type=int,
        default=3,
        help="Number of most recent runs that must pass",
    )
    args = parser.parse_args()

    rc_dir = Path(args.artifact_base).resolve() / args.rc_tag
    if not rc_dir.exists():
        print(json.dumps({"pass": False, "reason": "rc_tag_directory_missing", "rc_dir": str(rc_dir)}))
        return 1

    runs = _collect_runs(rc_dir)
    required = int(args.required_passes)
    if len(runs) < required:
        print(
            json.dumps(
                {
                    "pass": False,
                    "reason": "insufficient_runs",
                    "required": required,
                    "found": len(runs),
                }
            )
        )
        return 1

    recent = runs[-required:]
    failures = []
    checked = []
    for run_name, run_dir in recent:
        validation_path = run_dir / "soak_validation.json"
        if not validation_path.exists():
            failures.append({"run": run_name, "reason": "missing_soak_validation_json"})
            checked.append({"run": run_name, "pass": False})
            continue
        try:
            validation = _load_validation(validation_path)
            passed = bool(validation.get("pass"))
            checked.append({"run": run_name, "pass": passed})
            if not passed:
                failures.append(
                    {
                        "run": run_name,
                        "reason": "validation_failed",
                        "validation_reasons": validation.get("reasons", []),
                    }
                )
        except Exception as exc:  # pragma: no cover
            failures.append({"run": run_name, "reason": f"validation_read_error: {exc}"})
            checked.append({"run": run_name, "pass": False})

    result = {
        "pass": len(failures) == 0,
        "rc_tag": args.rc_tag,
        "required_passes": required,
        "checked_runs": checked,
        "failures": failures,
    }
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
