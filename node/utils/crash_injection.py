"""Optional crash-injection hooks for process-level recovery tests."""

from __future__ import annotations

import os


def maybe_crash(point: str) -> None:
    target = str(os.getenv("BERZCOIN_CRASH_POINT", "") or "").strip()
    if not target:
        return
    if target == str(point):
        os._exit(137)
