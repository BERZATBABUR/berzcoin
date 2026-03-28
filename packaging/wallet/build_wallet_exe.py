#!/usr/bin/env python3
"""Build a one-file ``berzcoin-wallet`` executable with PyInstaller.

Usage::

    pip install pyinstaller
    python packaging/wallet/build_wallet_exe.py

Extra PyInstaller CLI args after ``--``::

    python packaging/wallet/build_wallet_exe.py -- --log-level DEBUG

Optional branding (Windows): place ``assets/berzcoin.ico`` and
``packaging/wallet/version_info.txt`` (PE-version resource text as in PyInstaller
docs); they are applied only when present.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    entry = repo_root / "cli" / "wallet_standalone.py"
    packaging_dir = repo_root / "packaging" / "wallet"
    dist_dir = repo_root / "dist"
    work_dir = repo_root / "build" / "wallet_pyinstaller"
    assets_dir = repo_root / "assets"
    version_file = packaging_dir / "version_info.txt"

    if not entry.is_file():
        print(f"Entry script not found: {entry}", file=sys.stderr)
        sys.exit(1)

    exe_name = "berzcoin-wallet.exe" if platform.system() == "Windows" else "berzcoin-wallet"

    args: list[str] = [
        str(entry),
        "--name",
        "berzcoin-wallet",
        "--onefile",
        "--console",
        "--clean",
        "--noconfirm",
        f"--distpath={dist_dir}",
        f"--workpath={work_dir}",
        f"--specpath={packaging_dir}",
        f"--paths={repo_root}",
        "--collect-all",
        "cryptography",
        "--collect-submodules",
        "node.wallet",
        "--collect-submodules",
        "shared",
        "--hidden-import",
        "argparse",
        "--hidden-import",
        "urllib.request",
        "--hidden-import",
        "node.wallet.simple_wallet",
    ]

    icon = assets_dir / "berzcoin.ico"
    if icon.is_file():
        args.extend(["--icon", str(icon)])

    if platform.system() == "Windows" and version_file.is_file():
        args.extend(["--version-file", str(version_file)])

    if "--" in sys.argv:
        idx = sys.argv.index("--")
        args.extend(sys.argv[idx + 1 :])

    cmd = [sys.executable, "-m", "PyInstaller", *args]
    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, cwd=str(repo_root))
    except subprocess.CalledProcessError as e:
        print(f"PyInstaller failed with exit code {e.returncode}", file=sys.stderr)
        print("Install PyInstaller: pip install pyinstaller", file=sys.stderr)
        sys.exit(e.returncode or 1)
    print(f"Built: {dist_dir / exe_name}")


if __name__ == "__main__":
    main()
