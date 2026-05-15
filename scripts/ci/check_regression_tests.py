#!/usr/bin/env python3
"""Basic regression-test policy checks."""

from __future__ import annotations

import ast
from pathlib import Path


def _iter_tests(root: Path):
    for p in root.rglob("test_*.py"):
        if p.is_file():
            yield p


def main() -> int:
    root = Path("tests")
    failures: list[str] = []
    for path in _iter_tests(root):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            has_regression_marker = False
            for dec in node.decorator_list:
                text = ast.unparse(dec)
                if "regression" in text:
                    has_regression_marker = True
                    break
            if has_regression_marker and "regression" not in node.name.lower():
                failures.append(
                    f"{path}:{node.lineno} regression-marked test should include 'regression' in name"
                )

    if failures:
        print("Regression policy check failed:")
        for line in failures:
            print(f"- {line}")
        return 1

    print("Regression policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

