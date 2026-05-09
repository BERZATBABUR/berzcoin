"""User-friendly BerzCoin launcher commands."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    tomllib = None


# Mode 3: simple seed list (can be overridden/extended via CLI flags).
SEED_NODES = [
    "seed.berzcoin.net:8333",
]


def _default_rpc_port(p2p_port: int) -> int:
    """Derive a stable RPC port from the selected P2P port."""
    return int(p2p_port) + 1000


def _build_config(
    *,
    datadir: Path,
    network: str,
    port: int,
    rpcport: int,
    connect_peers: List[str],
    addnode_peers: List[str],
) -> Path:
    datadir.mkdir(parents=True, exist_ok=True)
    conf_path = datadir / "berzcoin.auto.conf"

    lines = [
        "[main]",
        f"network = {network}",
        f"datadir = {datadir}",
        f"port = {int(port)}",
        "rpcbind = 127.0.0.1",
        f"rpcport = {int(rpcport)}",
        "dnsseed = false",
        "allow_missing_bootstrap = true",
    ]
    if connect_peers:
        lines.append(f"connect = {','.join(connect_peers)}")
    elif addnode_peers:
        lines.append(f"addnode = {','.join(addnode_peers)}")

    conf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return conf_path


def _load_toml_config(path: Path) -> Dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("TOML config loading requires Python 3.11+")
    if not path.is_file():
        raise RuntimeError(f"Config file not found: {path}")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise RuntimeError(f"Failed to parse TOML config {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid TOML config root in {path}")
    return data


def _resolve_start_settings(args: argparse.Namespace) -> Dict[str, Any]:
    file_network = None
    file_port = None
    file_datadir = None
    file_peers: List[str] = []

    if args.config:
        cfg_path = Path(os.path.expanduser(args.config)).resolve()
        data = _load_toml_config(cfg_path)
        node_cfg = data.get("node", {})
        p2p_cfg = data.get("p2p", {})
        if isinstance(node_cfg, dict):
            file_network = node_cfg.get("network")
            file_port = node_cfg.get("port")
            file_datadir = node_cfg.get("data_dir")
        if isinstance(p2p_cfg, dict):
            peers = p2p_cfg.get("peers", [])
            if isinstance(peers, list):
                file_peers = [str(p).strip() for p in peers if str(p).strip()]

    network = str(args.network or file_network or "mainnet")
    if network not in ("mainnet", "testnet", "regtest"):
        raise RuntimeError(f"Invalid network: {network}")

    port = int(args.port if args.port is not None else (file_port if file_port is not None else 8333))
    data_dir_raw = args.data_dir if args.data_dir is not None else (file_datadir if file_datadir is not None else "~/.berzcoin")
    datadir = Path(os.path.expanduser(str(data_dir_raw)))
    rpcport = int(args.rpc_port) if args.rpc_port is not None else _default_rpc_port(port)

    connect_peers = list(args.connect or file_peers or [])
    use_seeds = bool(args.use_seeds)
    if connect_peers:
        # connect mode is explicit/manual and takes precedence over seed mode.
        addnode_peers: List[str] = []
    else:
        addnode_peers = list(SEED_NODES)
        if args.seed:
            addnode_peers.extend([str(s).strip() for s in args.seed if str(s).strip()])
        if not use_seeds:
            addnode_peers = []
        # de-duplicate while preserving order.
        seen = set()
        addnode_peers = [p for p in addnode_peers if not (p in seen or seen.add(p))]

    return {
        "network": network,
        "port": port,
        "rpcport": rpcport,
        "datadir": datadir,
        "connect_peers": connect_peers,
        "addnode_peers": addnode_peers,
    }


def _cmd_node_start(args: argparse.Namespace) -> int:
    settings = _resolve_start_settings(args)
    datadir = settings["datadir"]
    network = settings["network"]
    port = settings["port"]
    rpcport = settings["rpcport"]
    connect_peers = settings["connect_peers"]
    addnode_peers = settings["addnode_peers"]

    conf_path = _build_config(
        datadir=datadir,
        network=network,
        port=port,
        rpcport=rpcport,
        connect_peers=connect_peers,
        addnode_peers=addnode_peers,
    )

    print(f"Starting BerzCoin node")
    print(f"- network: {network}")
    print(f"- datadir: {datadir}")
    print(f"- p2p: 0.0.0.0:{port}")
    print(f"- rpc: 127.0.0.1:{rpcport}")
    if args.config:
        print(f"- config-source: {Path(os.path.expanduser(args.config)).resolve()}")
    if connect_peers:
        print(f"- connect: {', '.join(connect_peers)}")
    elif addnode_peers:
        print(f"- seed/addnode: {', '.join(addnode_peers)}")
    print(f"- conf: {conf_path}")
    if args.dry_run:
        print("- dry-run: true (node not started)")
        return 0

    cmd = [sys.executable, "-m", "node.app.main", "-conf", str(conf_path), "-datadir", str(datadir)]
    if network == "regtest":
        cmd.append("--regtest")
    elif network == "testnet":
        cmd.append("--testnet")

    os.execv(sys.executable, cmd)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BerzCoin easy launcher")
    subparsers = parser.add_subparsers(dest="group", required=True)

    node = subparsers.add_parser("node", help="Node lifecycle commands")
    node_subparsers = node.add_subparsers(dest="node_cmd", required=True)

    start = node_subparsers.add_parser("start", help="Start node (manual/config/seed modes)")
    start.add_argument("--config", help="TOML config file (Mode 2), e.g. configs/node.toml")
    start.add_argument("--network", choices=["mainnet", "testnet", "regtest"], default=None)
    start.add_argument("--port", type=int, default=None, help="P2P listening port")
    start.add_argument("--rpc-port", type=int, dest="rpc_port", help="RPC port (default: p2p+1000)")
    start.add_argument(
        "--data-dir",
        default=None,
        help="Node data directory (default: ~/.berzcoin)",
    )
    start.add_argument(
        "--connect",
        action="append",
        default=[],
        help="Connect-only peer in host:port format (Mode 1, repeatable)",
    )
    start.add_argument(
        "--seed",
        action="append",
        default=[],
        help="Extra seed/addnode peer in host:port format (Mode 3, repeatable)",
    )
    start.add_argument(
        "--no-seeds",
        action="store_false",
        dest="use_seeds",
        help="Disable seed/addnode fallback when no --connect peers are provided",
    )
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved startup config and exit",
    )
    start.set_defaults(use_seeds=True)
    start.set_defaults(func=_cmd_node_start)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    code = args.func(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
