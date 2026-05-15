#!/usr/bin/env python3
"""Fail CI if critical module coverage drops below policy thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "shared/consensus": 70.0,
    "node/validation": 60.0,
    "node/storage": 55.0,
    "shared/script": 75.0,
    "node/chain": 55.0,
}


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _pct_from_summary(summary: dict) -> float:
    pct = summary.get("percent_covered")
    if pct is None:
        return 0.0
    return float(pct)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", default="coverage.json")
    parser.add_argument("--min-total", type=float, default=55.0)
    args = parser.parse_args()

    payload = _load_json(Path(args.coverage_json))
    totals = payload.get("totals", {})
    total_pct = _pct_from_summary(totals)
    errors: list[str] = []

    if total_pct < float(args.min_total):
        errors.append(f"total coverage {total_pct:.2f}% < required {args.min_total:.2f}%")

    files = payload.get("files", {})
    for module_prefix, threshold in DEFAULT_THRESHOLDS.items():
        matched = [
            _pct_from_summary(meta.get("summary", {}))
            for rel_path, meta in files.items()
            if str(rel_path).startswith(module_prefix + "/") or str(rel_path) == module_prefix
        ]
        if not matched:
            errors.append(f"no coverage entries matched prefix: {module_prefix}")
            continue
        avg = sum(matched) / float(len(matched))
        if avg < float(threshold):
            errors.append(
                f"{module_prefix} average coverage {avg:.2f}% < required {threshold:.2f}%"
            )

    if errors:
        print("Coverage quality gate failed:")
        for line in errors:
            print(f"- {line}")
        return 1

    print("Coverage quality gate passed.")
    print(f"- total: {total_pct:.2f}%")
    for module_prefix, threshold in DEFAULT_THRESHOLDS.items():
        matched = [
            _pct_from_summary(meta.get("summary", {}))
            for rel_path, meta in files.items()
            if str(rel_path).startswith(module_prefix + "/") or str(rel_path) == module_prefix
        ]
        if matched:
            avg = sum(matched) / float(len(matched))
            print(f"- {module_prefix}: {avg:.2f}% (min {threshold:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
