#!/usr/bin/env python3
"""Static flaky-test guard for deterministic test policy."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PATTERNS = [
    ("time.sleep(", "use polling helper/asyncio or mark with @pytest.mark.allow_sleep"),
    ("random.random(", "seeded RNG required"),
    ("random.randint(", "seeded RNG required"),
]


def _iter_test_files(root: Path):
    for p in root.rglob("test_*.py"):
        if p.is_file():
            yield p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-root", default="tests")
    args = parser.parse_args()

    root = Path(args.tests_root)
    failures: list[str] = []
    for path in _iter_test_files(root):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        has_allow_sleep = "pytest.mark.allow_sleep" in lower or "allow_sleep" in lower
        has_seeded_random = bool(re.search(r"random\.seed\(|random\.random\(\d+\)|random\.Random\(", text))
        for token, hint in PATTERNS:
            if token not in text:
                continue
            if token == "time.sleep(" and has_allow_sleep:
                continue
            if token.startswith("random.") and has_seeded_random:
                continue
            failures.append(f"{path}: found `{token}` without guard ({hint})")

    if failures:
        print("Flaky-pattern guard failed:")
        for line in failures:
            print(f"- {line}")
        return 1
    print("Flaky-pattern guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

