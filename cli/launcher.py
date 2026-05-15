"""User-friendly BerzCoin launcher commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
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


def _registry_is_http(url_or_path: str) -> bool:
    parsed = urllib.parse.urlparse(url_or_path)
    return parsed.scheme in ("http", "https")


def _registry_is_file_url(url_or_path: str) -> bool:
    parsed = urllib.parse.urlparse(url_or_path)
    return parsed.scheme == "file"


def _registry_path(url_or_path: str) -> Path:
    if _registry_is_file_url(url_or_path):
        parsed = urllib.parse.urlparse(url_or_path)
        return Path(urllib.request.url2pathname(parsed.path)).expanduser().resolve()
    return Path(os.path.expanduser(url_or_path)).resolve()


def _load_registry_peers(url_or_path: str) -> List[str]:
    def _normalize_peers(payload: object) -> List[str]:
        # Supports:
        # 1) {"peers": ["ip:port", ...]}
        # 2) {"peers": [{"peer":"ip:port","status":"approved"}, ...]}
        # 3) {"peers": {"ip:port": {"status":"approved", ...}, ...}}
        # 4) legacy {"peers": ["ip:port", ...]} file-only mode
        if not isinstance(payload, dict):
            return []
        peers = payload.get("peers", [])
        out: List[str] = []
        if isinstance(peers, list):
            for item in peers:
                if isinstance(item, str):
                    val = item.strip()
                    if val:
                        out.append(val)
                    continue
                if isinstance(item, dict):
                    peer = str(item.get("peer", "")).strip()
                    status = str(item.get("status", "verified")).strip().lower()
                    if peer and status in {"verified", "approved"}:
                        out.append(peer)
            return out
        if isinstance(peers, dict):
            for peer, meta in peers.items():
                p = str(peer).strip()
                if not p:
                    continue
                if isinstance(meta, dict):
                    status = str(meta.get("status", "verified")).strip().lower()
                    if status not in {"verified", "approved"}:
                        continue
                out.append(p)
            return out
        return []

    if _registry_is_http(url_or_path):
        req = urllib.request.Request(url_or_path.rstrip("/") + "/peers", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return _normalize_peers(payload)

    path = _registry_path(url_or_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_peers(data)


def _register_registry_peer(url_or_path: str, peer: str) -> None:
    if _registry_is_http(url_or_path):
        body = json.dumps({"peer": peer}).encode("utf-8")
        req = urllib.request.Request(
            url_or_path.rstrip("/") + "/register",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            return

    path = _registry_path(url_or_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    else:
        data = {}

    peers = data.get("peers", {})
    if isinstance(peers, list):
        # Legacy list format upgrade.
        now = int(time.time())
        peers = {str(p): {"status": "approved", "first_seen": now, "last_seen": now} for p in peers if str(p).strip()}
    if not isinstance(peers, dict):
        peers = {}
    now = int(time.time())
    prev = peers.get(peer, {})
    if not isinstance(prev, dict):
        prev = {}
    peers[peer] = {
        "status": str(prev.get("status", "verified")),
        "first_seen": int(prev.get("first_seen", now)),
        "last_seen": now,
        "reason": str(prev.get("reason", "local_register")),
    }
    data["peers"] = peers
    data["updated_at"] = int(time.time())
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
    if not 1 <= port <= 65535:
        raise RuntimeError(f"Invalid port: {port}")
    data_dir_raw = args.data_dir if args.data_dir is not None else (file_datadir if file_datadir is not None else "~/.berzcoin")
    datadir = Path(os.path.expanduser(str(data_dir_raw)))
    rpcport = int(args.rpc_port) if args.rpc_port is not None else _default_rpc_port(port)
    if not 1 <= rpcport <= 65535:
        raise RuntimeError(f"Invalid rpc port: {rpcport}")
    if port == rpcport:
        raise RuntimeError("P2P port and RPC port must differ")

    connect_peers = list(args.connect or file_peers or [])
    use_seeds = bool(args.use_seeds)
    addnode_peers: List[str] = []

    if args.auto_discover:
        if not args.self_ip:
            raise RuntimeError("--auto-discover requires --self-ip (public/LAN reachable IP)")
        try:
            ipaddress.ip_address(str(args.self_ip).strip())
        except ValueError as e:
            raise RuntimeError(f"Invalid --self-ip value: {args.self_ip}") from e
        if int(args.max_discovery_peers) <= 0:
            raise RuntimeError("--max-discovery-peers must be > 0")
        self_peer = f"{args.self_ip}:{port}"
        try:
            _register_registry_peer(args.seed_registry, self_peer)
            discovered = _load_registry_peers(args.seed_registry)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f"Seed registry error: {e}") from e

        discovered = [p for p in discovered if p and p != self_peer]
        # Auto-discovery populates addnode peers so node can still use its regular discovery flow.
        addnode_peers = discovered[: max(1, int(args.max_discovery_peers))]

    if connect_peers:
        # connect mode is explicit/manual and takes precedence over seed mode.
        addnode_peers = []
    else:
        if not addnode_peers:
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
    if args.auto_discover:
        print(f"- auto-discover: enabled")
        print(f"- registry: {args.seed_registry}")
        print(f"- self-peer: {args.self_ip}:{port}")
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


def _cmd_node_join(args: argparse.Namespace) -> int:
    """One-command UX shortcut for joining a network via seed registry."""
    if not hasattr(args, "config"):
        args.config = None
    args.auto_discover = True
    args.use_seeds = True
    args.connect = []
    args.seed = []
    return _cmd_node_start(args)


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
        "--auto-discover",
        action="store_true",
        help="Mode 4: register this node and fetch peers from seed registry",
    )
    start.add_argument(
        "--seed-registry",
        default="~/.berzcoin/seed_registry.json",
        help="Seed registry path/URL. Supports local path, file://, or http(s)://host:port",
    )
    start.add_argument(
        "--self-ip",
        help="Reachable IP to register in seed registry (required with --auto-discover)",
    )
    start.add_argument(
        "--max-discovery-peers",
        type=int,
        default=8,
        help="Max peers imported from registry in auto-discovery mode",
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

    join = node_subparsers.add_parser("join", help="Quick join via seed registry")
    join.add_argument("--network", choices=["mainnet", "testnet", "regtest"], default="mainnet")
    join.add_argument("--port", type=int, default=8333, help="P2P listening port")
    join.add_argument("--rpc-port", type=int, dest="rpc_port", help="RPC port (default: p2p+1000)")
    join.add_argument(
        "--data-dir",
        default="~/.berzcoin",
        help="Node data directory",
    )
    join.add_argument(
        "--seed-registry",
        required=True,
        help="Seed registry path/URL. Supports local path, file://, or http(s)://host:port",
    )
    join.add_argument(
        "--self-ip",
        required=True,
        help="Reachable IP to register in seed registry",
    )
    join.add_argument(
        "--max-discovery-peers",
        type=int,
        default=8,
        help="Max peers imported from registry",
    )
    join.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved startup config and exit",
    )
    join.set_defaults(func=_cmd_node_join)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    code = args.func(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
