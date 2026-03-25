#!/usr/bin/env python3
"""Resolve public DNS seeds to IPv4 endpoints and write ``configs/bootstrap_nodes.json``.

Uses only the standard library (no dnspython). IPv4 ``host:port`` entries are
written so they match the simple ``host:port`` parsing used by the P2P stack.

Run from anywhere::

    python scripts/update_dns_seeds.py
    python scripts/update_dns_seeds.py seed.custom.org --port 8333
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path
from typing import List, Set

DEFAULT_SEEDS = [
    "seed1.berzcoin.org",
    "seed2.berzcoin.org",
    "seed3.berzcoin.org",
    "dnsseed.berzcoin.org",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_seed(hostname: str, port: int) -> tuple[Set[str], str | None]:
    """Return a set of ``ipv4:port`` strings and an error message if none."""
    out: Set[str] = set()
    err: str | None = None
    try:
        infos = socket.getaddrinfo(
            hostname,
            port,
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as e:
        return out, str(e)

    for _family, _type, _proto, _canon, sockaddr in infos:
        if len(sockaddr) >= 2:
            ip = sockaddr[0]
            out.add(f"{ip}:{sockaddr[1]}")

    if not out:
        err = "no A/IPv4 records"
    return out, err


def update_seeds(seeds: List[str], port: int, output: Path) -> int:
    all_nodes: Set[str] = set()
    per_seed: dict[str, dict[str, object]] = {}

    for seed in seeds:
        nodes, err = resolve_seed(seed, port)
        all_nodes |= nodes
        if err:
            per_seed[seed] = {"status": "error", "error": err, "count": 0}
            print(f"[FAIL] {seed}: {err}")
        else:
            per_seed[seed] = {"status": "ok", "count": len(nodes), "nodes": sorted(nodes)}
            print(f"[OK] {seed}: {len(nodes)} node(s)")

    output.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "dns_seeds": seeds,
        "bootstrap_nodes": sorted(all_nodes),
        "p2p_port": port,
        "last_updated": int(time.time()),
        "per_seed": per_seed,
    }
    output.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\n[OK] Wrote {output} ({len(all_nodes)} unique bootstrap node(s))")
    if not all_nodes:
        print("[WARN] No addresses resolved; check DNS or seed hostnames.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "seeds",
        nargs="*",
        default=DEFAULT_SEEDS,
        help="DNS seed hostnames (default: packaged BerzCoin seed list)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8333,
        help="P2P port recorded in bootstrap addresses (default 8333)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON output path (default: <repo>/configs/bootstrap_nodes.json)",
    )
    args = parser.parse_args()
    out = args.output if args.output else repo_root() / "configs" / "bootstrap_nodes.json"
    sys.exit(update_seeds(list(args.seeds), args.port, out))


if __name__ == "__main__":
    main()
