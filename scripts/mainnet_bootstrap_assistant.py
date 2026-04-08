#!/usr/bin/env python3
"""Mainnet bootstrap assistant for BerzCoin deployment.

Creates a production-oriented `berzcoin.conf` and optional `bootstrap_nodes.json`
in a target datadir with safety checks for peer discovery configuration.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Iterable, List

from update_dns_seeds import update_seeds


PLACEHOLDER_SEEDS = {
    "seed1.berzcoin.org",
    "seed2.berzcoin.org",
    "seed3.berzcoin.org",
    "dnsseed.berzcoin.org",
}


def _split_csv_values(values: Iterable[str] | None) -> List[str]:
    out: List[str] = []
    if not values:
        return out
    for item in values:
        for part in str(item).split(","):
            val = part.strip()
            if val:
                out.append(val)
    deduped: List[str] = []
    seen = set()
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _contains_placeholder_seed(seeds: Iterable[str]) -> bool:
    for seed in seeds:
        if seed.strip().lower() in PLACEHOLDER_SEEDS:
            return True
    return False


def _bootstrap_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    nodes = data.get("bootstrap_nodes", [])
    if isinstance(nodes, list):
        return len(nodes)
    return 0


def _build_config_text(
    *,
    datadir: Path,
    bind: str,
    port: int,
    rpcbind: str,
    rpcport: int,
    rpcallowip: str,
    dnsseed: bool,
    dnsseeds: List[str],
    addnode: List[str],
    connect: List[str],
    bootstrap_enabled: bool,
    bootstrap_file: str,
    allow_missing_bootstrap: bool,
) -> str:
    dnsseeds_text = ",".join(dnsseeds)
    addnode_text = ",".join(addnode)
    connect_text = ",".join(connect)
    return f"""[main]
network = mainnet
datadir = {datadir}

bind = {bind}
port = {port}
maxconnections = 125
maxoutbound = 8

bootstrap_enabled = {"true" if bootstrap_enabled else "false"}
bootstrap_file = {bootstrap_file}
allow_missing_bootstrap = {"true" if allow_missing_bootstrap else "false"}
dnsseed = {"true" if dnsseed else "false"}
dnsseeds = {dnsseeds_text}
addnode = {addnode_text}
connect = {connect_text}
network_hardening = false

rpcbind = {rpcbind}
rpcport = {rpcport}
rpcallowip = {rpcallowip}
rpcthreads = 4
rpcworkqueue = 16
rpctimeout = 30
rpc_require_auth = true

activation_height_berz_softfork_bip34_strict = 180000
activation_height_berz_hardfork_tx_v2 = 180100
node_consensus_version = 2
enforce_hardfork_guardrails = true

wallet = default
disablewallet = false
wallet_private_key =

mining = false
autominer = false
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a safe mainnet config and bootstrap peers for BerzCoin."
    )
    parser.add_argument("--datadir", required=True, help="Target datadir, e.g. ~/.berzcoin-mainnet-a")
    parser.add_argument("--config-name", default="berzcoin.conf", help="Config filename inside datadir")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8333)
    parser.add_argument("--rpcbind", default="127.0.0.1")
    parser.add_argument("--rpcport", type=int, default=8332)
    parser.add_argument(
        "--rpcallowip",
        default="127.0.0.1",
        help="Comma-separated list, e.g. 127.0.0.1,10.0.0.0/8",
    )

    parser.add_argument("--addnode", action="append", help="One or many host:port peers (csv allowed)")
    parser.add_argument("--connect", action="append", help="Connect-only peers (csv allowed)")
    parser.add_argument("--dnsseed", action="store_true", help="Enable DNS seed discovery")
    parser.add_argument("--dnsseeds", action="append", help="DNS seed hostnames (csv allowed)")

    parser.add_argument(
        "--bootstrap-source",
        help="Path to an existing bootstrap_nodes.json to copy into datadir",
    )
    parser.add_argument(
        "--bootstrap-from-seeds",
        action="append",
        help="Resolve seed hostnames into bootstrap file (csv allowed)",
    )
    parser.add_argument(
        "--bootstrap-file-name",
        default="bootstrap_nodes.json",
        help="Bootstrap filename inside datadir",
    )
    parser.add_argument(
        "--probe-timeout-secs",
        type=float,
        default=2.0,
        help="TCP probe timeout used when generating bootstrap from seeds",
    )
    parser.add_argument(
        "--require-reachable",
        action="store_true",
        help="Keep only TCP-reachable endpoints when generating bootstrap",
    )
    parser.add_argument(
        "--allow-missing-bootstrap",
        action="store_true",
        help="Bypass startup safety check when no discovery source is configured",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing config file",
    )
    args = parser.parse_args()

    datadir = Path(args.datadir).expanduser().resolve()
    datadir.mkdir(parents=True, exist_ok=True)
    config_path = datadir / args.config_name
    bootstrap_path = datadir / args.bootstrap_file_name

    addnode = _split_csv_values(args.addnode)
    connect = _split_csv_values(args.connect)
    dnsseeds = _split_csv_values(args.dnsseeds)
    bootstrap_from_seeds = _split_csv_values(args.bootstrap_from_seeds)

    if _contains_placeholder_seed(dnsseeds) or _contains_placeholder_seed(bootstrap_from_seeds):
        print(
            "[ERROR] Placeholder seed hostnames are not allowed for mainnet deployment.",
            file=sys.stderr,
        )
        return 2

    if args.bootstrap_source and bootstrap_from_seeds:
        print(
            "[ERROR] Use either --bootstrap-source or --bootstrap-from-seeds, not both.",
            file=sys.stderr,
        )
        return 2

    if args.bootstrap_source:
        src = Path(args.bootstrap_source).expanduser().resolve()
        if not src.is_file():
            print(f"[ERROR] bootstrap source not found: {src}", file=sys.stderr)
            return 2
        shutil.copyfile(src, bootstrap_path)
        print(f"[OK] Copied bootstrap file -> {bootstrap_path}")

    if bootstrap_from_seeds:
        rc = update_seeds(
            seeds=bootstrap_from_seeds,
            port=int(args.port),
            output=bootstrap_path,
            probe_timeout_secs=max(0.0, float(args.probe_timeout_secs)),
            require_reachable=bool(args.require_reachable),
        )
        if rc != 0:
            print("[ERROR] Failed to generate bootstrap file from DNS seeds", file=sys.stderr)
            return rc

    bootstrap_nodes_count = _bootstrap_count(bootstrap_path)
    bootstrap_enabled = bootstrap_nodes_count > 0

    discovery_ok = bool(connect or addnode or bootstrap_enabled or (args.dnsseed and dnsseeds))
    if not discovery_ok and not args.allow_missing_bootstrap:
        print(
            "[ERROR] No discovery source configured. Provide connect/addnode/bootstrap/dnsseeds "
            "or pass --allow-missing-bootstrap.",
            file=sys.stderr,
        )
        return 2

    if args.dnsseed and not dnsseeds:
        print("[WARN] dnsseed enabled but no dnsseeds provided; relying on other discovery sources.")

    if config_path.exists() and not args.force:
        print(f"[ERROR] Config already exists: {config_path} (use --force to overwrite)", file=sys.stderr)
        return 2

    text = _build_config_text(
        datadir=datadir,
        bind=str(args.bind),
        port=int(args.port),
        rpcbind=str(args.rpcbind),
        rpcport=int(args.rpcport),
        rpcallowip=str(args.rpcallowip),
        dnsseed=bool(args.dnsseed),
        dnsseeds=dnsseeds,
        addnode=addnode,
        connect=connect,
        bootstrap_enabled=bootstrap_enabled,
        bootstrap_file=args.bootstrap_file_name,
        allow_missing_bootstrap=bool(args.allow_missing_bootstrap),
    )
    config_path.write_text(text, encoding="utf-8")
    config_path.chmod(0o600)

    print("")
    print("[OK] Mainnet bootstrap assistant completed")
    print(f"  datadir:         {datadir}")
    print(f"  config:          {config_path}")
    print(f"  bootstrap file:  {bootstrap_path if bootstrap_enabled else '(disabled/not present)'}")
    print(f"  bootstrap nodes: {bootstrap_nodes_count}")
    print(f"  addnode count:   {len(addnode)}")
    print(f"  connect count:   {len(connect)}")
    print(f"  dnsseed:         {'enabled' if args.dnsseed else 'disabled'}")
    print("")
    print("Start command:")
    print(f"  python3 -m node.app.main -conf {config_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
