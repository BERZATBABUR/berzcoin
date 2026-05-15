#!/usr/bin/env python3
"""Create and print a private-key wallet for manual activation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create BerzCoin private-key wallet")
    parser.add_argument(
        "--datadir",
        default="~/.berzcoin",
        help="Data directory (default: ~/.berzcoin)",
    )
    args = parser.parse_args()

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from node.wallet.simple_wallet import SimpleWalletManager

    datadir = Path(os.path.expanduser(args.datadir))
    manager = SimpleWalletManager(datadir)
    wallet = manager.create_wallet()

    print("Wallet created:")
    print("Private key (store offline):")
    print(wallet.private_key_hex)
    print("Public key:")
    print(wallet.public_key_hex)
    print("Address:")
    print(wallet.address)
    print("Mnemonic:")
    print(wallet.mnemonic)
    print("")
    print("Activate on node:")
    print(f'berzcoin-cli -datadir "{datadir}" activatewallet "{wallet.private_key_hex}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
