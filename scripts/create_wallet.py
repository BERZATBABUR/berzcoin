#!/usr/bin/env python3
"""Create ~/.berzcoin/wallets/default for regtest (optional; berzcoind can create it via walletpassphrase).

The on-disk path must match ``wallet = default`` in berzcoin.conf: ``<datadir>/wallets/default`` (no .dat).

Usage (from repository root, after ``pip install -e .`` or with PYTHONPATH set)::

    export PYTHONPATH="$(pwd)"
    python scripts/create_wallet.py --password 'your-strong-secret'

Or::

    BERZCOIN_WALLET_PASSWORD='your-strong-secret' python scripts/create_wallet.py

Refuses to overwrite an existing wallet file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create BerzCoin encrypted wallet file (regtest)")
    parser.add_argument(
        "--datadir",
        default="~/.berzcoin",
        help="Data directory (default: ~/.berzcoin)",
    )
    parser.add_argument(
        "--network",
        default="regtest",
        choices=("mainnet", "testnet", "regtest"),
        help="Network label stored in the wallet",
    )
    parser.add_argument(
        "--name",
        default="default",
        help="Wallet name (file at <datadir>/wallets/<name>)",
    )
    parser.add_argument(
        "--password",
        default="",
        help="Encryption password (or set BERZCOIN_WALLET_PASSWORD)",
    )
    args = parser.parse_args()

    password = (args.password or os.environ.get("BERZCOIN_WALLET_PASSWORD", "")).strip()
    if not password:
        print(
            "Error: provide --password or set BERZCOIN_WALLET_PASSWORD.",
            file=sys.stderr,
        )
        return 1

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from node.wallet.core.wallet import Wallet

    datadir = Path(os.path.expanduser(args.datadir))
    wallet_dir = datadir / "wallets"
    wallet_dir.mkdir(parents=True, exist_ok=True)
    wallet_path = wallet_dir / args.name

    if wallet_path.exists():
        print(f"Error: wallet already exists: {wallet_path}", file=sys.stderr)
        print("Remove it only if you intend to destroy funds; otherwise use berzcoind to load it.", file=sys.stderr)
        return 1

    w = Wallet(str(wallet_path), args.network)
    mnemonic = w.create(password)
    if not w.unlock(password):
        print("Error: unlock failed after create.", file=sys.stderr)
        return 1
    first = w.get_new_address()

    print(f"Wallet created: {wallet_path}")
    print("Mnemonic (store safely, offline):")
    print(mnemonic)
    if first:
        print("First receiving address:", first)
    print("")
    print("Set the same string as walletpassphrase in berzcoin.conf if this node should load this wallet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
