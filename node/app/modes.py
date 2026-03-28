"""Operation modes for BerzCoin node."""

from enum import Enum
from typing import Any, Dict, List

from shared.utils.logging import get_logger

logger = get_logger()


class NodeMode(Enum):
    """Node operation modes."""

    FULL = "full"
    PRUNED = "pruned"
    LIGHT = "light"
    WALLET = "wallet"
    MINING = "mining"
    SEED = "seed"


class ModeManager:
    """Manage node operation modes."""

    def __init__(self, config: Any):
        self.config = config
        self.mode = self._determine_mode()

    def _determine_mode(self) -> NodeMode:
        if self.config.get("mining"):
            return NodeMode.MINING
        if self.config.get("disablewallet") is False and self.config.get("blocksonly"):
            return NodeMode.WALLET
        if self.config.get("prune", 0) > 0:
            return NodeMode.PRUNED
        if self.config.get("lightwallet"):
            logger.warning("lightwallet mode is disabled in this release; using full mode")
            self.config.set("lightwallet", False)
        return NodeMode.FULL

    def is_full_node(self) -> bool:
        return self.mode == NodeMode.FULL

    def is_pruned(self) -> bool:
        return self.mode == NodeMode.PRUNED

    def is_light_node(self) -> bool:
        return self.mode == NodeMode.LIGHT

    def has_wallet(self) -> bool:
        return not self.config.get("disablewallet") and self.mode != NodeMode.LIGHT

    def is_mining(self) -> bool:
        return self.mode == NodeMode.MINING

    def is_seed(self) -> bool:
        return self.mode == NodeMode.SEED

    def get_required_components(self) -> List[str]:
        components = ["database", "chainstate"]
        if self.is_full_node() or not self.is_light_node():
            components.extend(["p2p", "mempool", "blocks_store"])
        if self.has_wallet():
            components.append("wallet")
        if self.is_mining():
            components.append("mining")
        if self.config.get("txindex"):
            components.append("txindex")
        if self.config.get("addressindex"):
            components.append("addressindex")
        return components

    def get_component_config(self, component: str) -> dict:
        cfg: Dict[str, Dict[str, Any]] = {
            "database": {
                "data_dir": self.config.get_datadir(),
                "network": self.config.get("network"),
            },
            "chainstate": {
                "params": self.config.get_network_params(),
                "data_dir": self.config.get_datadir(),
            },
            "p2p": {
                "port": self.config.get("port"),
                "max_connections": self.config.get("maxconnections"),
                "max_outbound": self.config.get("maxoutbound"),
                "dns_seed": self.config.get("dnsseed"),
            },
            "mempool": {
                "max_size": self.config.get("maxmempool") * 1024 * 1024,
                "min_fee": self.config.get("mempoolminfee"),
            },
            "wallet": {
                "path": self.config.get_datadir() / "wallets" / self.config.get("wallet"),
                "network": self.config.get("network"),
                "private_key": self.config.get("wallet_private_key"),
            },
            "mining": {
                "address": self.config.get("miningaddress"),
                "threads": self.config.get("mining_threads", 1),
            },
            "txindex": {"enabled": self.config.get("txindex")},
            "addressindex": {"enabled": self.config.get("addressindex")},
        }
        return cfg.get(component, {})

    def get_description(self) -> str:
        descriptions = {
            NodeMode.FULL: "Full node with complete blockchain and all features",
            NodeMode.PRUNED: f"Pruned node (keeping {self.config.get('prune')} MB of blockchain)",
            NodeMode.LIGHT: "Light node (headers only, no full blocks)",
            NodeMode.WALLET: "Wallet-only mode (no blockchain sync)",
            NodeMode.MINING: "Mining mode with block generation",
            NodeMode.SEED: "Seed node (serves peer addresses only)",
        }
        return descriptions.get(self.mode, "Unknown mode")

    def __str__(self) -> str:
        return (
            f"ModeManager(mode={self.mode.value}, "
            f"components={self.get_required_components()})"
        )
