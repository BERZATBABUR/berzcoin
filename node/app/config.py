"""Configuration management for BerzCoin node."""

from __future__ import annotations

import configparser
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared.consensus.params import ConsensusParams
from shared.utils.logging import get_logger

logger = get_logger()


class Config:
    """Node configuration."""

    DEFAULT_CONFIG: Dict[str, Any] = {
        "network": "mainnet",
        "bind": "0.0.0.0",
        "port": 8333,
        "rpcbind": "127.0.0.1",
        "rpcport": 8332,
        "rpcuser": "",
        "rpcpassword": "",
        "rpcallowip": ["127.0.0.1"],
        "rpcthreads": 4,
        "rpcworkqueue": 16,
        "rpctimeout": 30,
        "datadir": "~/.berzcoin",
        "reindex": False,
        "prune": 0,
        "txindex": True,
        "addressindex": False,
        "maxmempool": 300,
        "mempoolminfee": 1000,
        "wallet": "default",
        "disablewallet": False,
        "wallet_private_key": "",
        "mining": False,
        "miningaddress": "",
        "autominer": False,
        "mining_threads": 1,
        # Coinbase spend maturity (confirmations). Keep 100 by default;
        # local v1 launcher can override for faster regtest demos.
        "coinbase_maturity": 100,
        # When true, miner is forced to stop/start only for the active wallet address.
        "mining_require_wallet_match": True,
        # Optional runtime mining pacing override (seconds per block).
        # 0 means "use consensus default for current network".
        "mining_target_time_secs": 0,
        "debug": False,
        "logfile": "debug.log",
        "maxconnections": 125,
        "maxoutbound": 8,
        # Sync / performance tuning knobs.
        "sync_poll_interval_secs": 30,
        "sync_error_backoff_secs": 60,
        "sync_getdata_batch_size": 128,
        "sync_block_request_timeout_secs": 30,
        "blocks_cache_size": 100,
        # Health / readiness thresholds.
        "health_sync_lag_warn_blocks": 24,
        "health_sync_lag_critical_blocks": 144,
        "health_min_peers_warn": 1,
        "health_max_mempool_txs_warn": 200000,
        "blocksonly": False,
        "lightwallet": False,
        "filterport": 8334,
        "checkpoints": True,
        "dnsseed": False,
        "dnsseeds": [],
        "addnode": [],
        "connect": [],
        "authority_chain_enabled": False,
        "authority_trusted_nodes": [],
        "bootstrap_file": "bootstrap_nodes.json",
        "bootstrap_enabled": True,
        "webdashboard": False,
        "webhost": "127.0.0.1",
        "webport": 8080,
        "web_require_auth": False,
        "rpc_require_auth": True,
        "disable_ip_discovery": False,
        # Operator safety: refuse to start a public-facing node when no
        # bootstrap peers are configured unless this flag is set to true.
        "allow_missing_bootstrap": False,
    }

    def __init__(self, config_path: Optional[str] = None):
        self.config = dict(self.DEFAULT_CONFIG)
        self.config_path = config_path
        if config_path:
            self.load(config_path)

    def load(self, config_path: str) -> bool:
        try:
            parser = configparser.ConfigParser()
            parser.read(config_path)
            for section in parser.sections():
                for key, value in parser.items(section):
                    if key == "listen":
                        self.config["bind"] = self._parse_value(value, self.config["bind"])
                        continue
                    if key in self.config:
                        self.config[key] = self._parse_value(value, self.config[key])
            logger.info("Loaded configuration from %s", config_path)
            return True
        except (OSError, configparser.Error) as e:
            logger.error("Failed to load config: %s", e)
            return False

    def _parse_value(self, value: str, default: Any) -> Any:
        if isinstance(default, bool):
            return value.lower() in ("true", "yes", "1")
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, list):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value

    def get_rpc_bind(self) -> str:
        """Address for the RPC listener; ties rpcbind to rpcallowip for safe defaults."""
        rpcbind = str(self.config.get("rpcbind", "127.0.0.1"))
        allowips = self._normalize_rpcallowip(self.config.get("rpcallowip", ["127.0.0.1"]))
        if allowips == ["127.0.0.1"]:
            return "127.0.0.1"
        return rpcbind

    def is_rpc_allowed(self, client_ip: str) -> bool:
        """Return True if client_ip may use JSON-RPC (after TCP accept)."""
        allowips = self._normalize_rpcallowip(self.config.get("rpcallowip", ["127.0.0.1"]))
        if "*" in allowips:
            return True
        if not allowips:
            return False
        for allowed in allowips:
            if self._ip_matches(client_ip, allowed):
                return True
        return False

    def _normalize_rpcallowip(self, raw: Union[str, List[str], None]) -> List[str]:
        if raw is None:
            return ["127.0.0.1"]
        if isinstance(raw, str):
            return [v.strip() for v in raw.split(",") if v.strip()]
        return list(raw)

    def _ip_matches(self, client_ip: str, allowed: str) -> bool:
        """Exact IPv4/IPv6 match or CIDR membership."""
        if client_ip == allowed:
            return True
        if "/" in allowed:
            try:
                network = ipaddress.ip_network(allowed, strict=False)
                addr = ipaddress.ip_address(client_ip.split("%")[0])
                return addr in network
            except ValueError:
                return False
        return False

    def get_datadir(self) -> Path:
        return Path(os.path.expanduser(str(self.config["datadir"])))

    def _peer_list(self, key: str) -> List[str]:
        raw = self.config.get(key, [])
        if isinstance(raw, str):
            return [p.strip() for p in raw.split(",") if p.strip()]
        if isinstance(raw, list):
            return [str(p).strip() for p in raw if str(p).strip()]
        return []

    def get_addnode_peers(self) -> List[str]:
        """Static peers to try in addition to DNS / P2P addr (comma-separated in INI)."""
        return self._peer_list("addnode")

    def get_connect_peers(self) -> List[str]:
        """If non-empty, only these peers are used (no DNS discovery)."""
        return self._peer_list("connect")

    def is_connect_only(self) -> bool:
        return bool(self.get_connect_peers())

    def get_bootstrap_nodes(self) -> List[str]:
        """Load ``bootstrap_nodes`` list from JSON under datadir (or absolute path)."""
        if not self.config.get("bootstrap_enabled", True):
            return []
        name = str(self.config.get("bootstrap_file", "bootstrap_nodes.json"))
        path = Path(name)
        if not path.is_absolute():
            path = self.get_datadir() / name
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read bootstrap file %s: %s", path, e)
            return []
        nodes = data.get("bootstrap_nodes", [])
        if not isinstance(nodes, list):
            return []
        return [str(x).strip() for x in nodes if str(x).strip()]

    def get_network_params(self) -> ConsensusParams:
        network = self.config["network"]
        if network == "mainnet":
            params = ConsensusParams.mainnet()
        elif network == "testnet":
            params = ConsensusParams.testnet()
        else:
            params = ConsensusParams.regtest()

        maturity = int(self.config.get("coinbase_maturity", getattr(params, "coinbase_maturity", 100)) or 0)
        setattr(params, "coinbase_maturity", max(0, maturity))
        return params

    def validate(self) -> bool:
        datadir = self.get_datadir()
        if not datadir.exists():
            try:
                datadir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.error("Cannot create data directory: %s", e)
                return False

        if self.config["network"] not in ("mainnet", "testnet", "regtest"):
            logger.error("Invalid network: %s", self.config["network"])
            return False

        port = int(self.config["port"])
        if not 1024 <= port <= 65535:
            logger.error("Invalid port: %s", port)
            return False

        rpcport = int(self.config["rpcport"])
        if not 1024 <= rpcport <= 65535:
            logger.error("Invalid rpcport: %s", rpcport)
            return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.config)

    def save(self, config_path: str) -> bool:
        try:
            parser = configparser.ConfigParser()
            parser["network"] = {
                k: str(v)
                for k, v in self.config.items()
                if k
                in (
                    "network",
                    "bind",
                    "port",
                    "rpcbind",
                    "rpcport",
                    "rpcallowip",
                    "rpcthreads",
                    "rpcworkqueue",
                    "rpctimeout",
                )
            }
            parser["wallet"] = {
                k: str(v)
                for k, v in self.config.items()
                if k in ("wallet", "disablewallet")
            }
            parser["mining"] = {
                k: str(v)
                for k, v in self.config.items()
                if k in ("mining", "miningaddress")
            }
            with open(config_path, "w", encoding="utf-8") as f:
                parser.write(f)
            logger.info("Saved configuration to %s", config_path)
            return True
        except OSError as e:
            logger.error("Failed to save config: %s", e)
            return False
