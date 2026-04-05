"""Generate a reproducibility manifest for a release candidate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = ROOT / "requirements-lock.txt"
OUT = ROOT / "release" / "manifest.json"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _pkg_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_lock_versions(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            continue
        name, ver = line.split("==", 1)
        out[name.strip().lower()] = ver.strip()
    return out


def main() -> int:
    commit_sha = _git("rev-parse", "HEAD")
    try:
        tag = _git("describe", "--tags", "--exact-match")
    except subprocess.CalledProcessError:
        tag = None

    lock_hash = _sha256_file(LOCKFILE) if LOCKFILE.exists() else None
    lock_versions = _read_lock_versions(LOCKFILE)

    def _tool_ver(name: str) -> str | None:
        installed = _pkg_version(name)
        if installed:
            return installed
        return lock_versions.get(name.lower())

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": "berzcoin",
        "commit_sha": commit_sha,
        "tag": tag,
        "python_version": sys.version.split()[0],
        "dependency_lock_file": str(LOCKFILE.relative_to(ROOT)) if LOCKFILE.exists() else None,
        "dependency_lock_sha256": lock_hash,
        "test_suite_versions": {
            "pytest": _pkg_version("pytest"),
            "pytest-asyncio": _pkg_version("pytest-asyncio"),
            "pytest-cov": _pkg_version("pytest-cov"),
        },
        "ci_tool_versions": {
            "black": _tool_ver("black"),
            "ruff": _tool_ver("ruff"),
            "flake8": _tool_ver("flake8"),
            "build": _tool_ver("build"),
            "wheel": _tool_ver("wheel"),
            "bandit": _tool_ver("bandit"),
            "pip-audit": _tool_ver("pip-audit"),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
