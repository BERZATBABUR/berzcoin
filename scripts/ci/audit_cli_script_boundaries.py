#!/usr/bin/env python3
"""Guardrails: CLI/scripts should use RPC paths, not mutate chainstate internals directly."""

from __future__ import annotations

from pathlib import Path


FORBIDDEN = (
    "utxo_store.",
    "block_index.",
    "db.execute(",
    "connect_block.connect(",
    "disconnect_block.disconnect(",
)

ALLOWLIST = {
    # Dedicated internal recovery tooling may touch lower layers directly.
    "scripts/internal",
    "scripts/ci",
}


def _iter_files() -> list[Path]:
    roots = [Path("cli"), Path("scripts")]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            out.append(p)
    return out


def main() -> int:
    failures: list[str] = []
    for path in _iter_files():
        ptxt = path.as_posix()
        if any(ptxt.startswith(prefix) for prefix in ALLOWLIST):
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                failures.append(f"{ptxt}: forbidden direct state mutation token `{token}`")
    if failures:
        print("CLI/script boundary audit failed:")
        for line in failures:
            print(f"- {line}")
        return 1
    print("CLI/script boundary audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

