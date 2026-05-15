"""Global pytest hardening: deterministic defaults, tier markers, and flaky guards."""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

import pytest


_FIXED_TEST_SEED = int(os.getenv("BERZ_TEST_SEED", "20260514"))


def pytest_configure(config: pytest.Config) -> None:
    # Stable hashing and pseudo-random behavior across test processes.
    os.environ.setdefault("PYTHONHASHSEED", str(_FIXED_TEST_SEED))
    random.seed(_FIXED_TEST_SEED)
    # Keep fuzz/chaos seeds deterministic unless test/CI overrides them.
    os.environ.setdefault("BERZ_FUZZ_SEED", "20260405")
    os.environ.setdefault("BERZ_MEMPOOL_FUZZ_SEED", "20260408")
    os.environ.setdefault("BERZ_CHAOS_SEED", "20260405")
    os.environ.setdefault("BERZ_CHAOS_INTEG_SEED", "20260406")
    os.environ.setdefault("BERZ_MEMPOOL_CHAOS_SEED", "20260407")
    os.environ.setdefault("BERZ_SOAK_SEED", "1337")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _ = config
    for item in items:
        p = Path(str(item.fspath)).as_posix()
        if "/tests/unit/" in p:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in p:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in p:
            item.add_marker(pytest.mark.e2e)
        elif "/tests/fuzz/" in p:
            item.add_marker(pytest.mark.fuzz)
            item.add_marker(pytest.mark.slow)
        elif "/tests/chaos/" in p:
            item.add_marker(pytest.mark.chaos)
            item.add_marker(pytest.mark.slow)

        # Real-process, soak, and long-run suites are always slow.
        nodeid = item.nodeid.lower()
        if "real_process" in nodeid or "soak" in nodeid or "long_run" in nodeid:
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def _guard_blocking_sleep(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Prevent uncontrolled blocking sleeps in deterministic suites.

    Use @pytest.mark.allow_sleep for tests that intentionally validate real-time behavior.
    """
    if request.node.get_closest_marker("allow_sleep") is not None:
        return

    def _forbidden_sleep(seconds: float) -> None:
        raise AssertionError(
            f"Blocking time.sleep({seconds}) is disallowed in tests; "
            "use asyncio primitives, polling helpers, or mark test with @pytest.mark.allow_sleep."
        )

    monkeypatch.setattr(time, "sleep", _forbidden_sleep)

