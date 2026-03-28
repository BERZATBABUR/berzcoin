"""Advanced mining control RPC handlers."""

from typing import Any, Dict, List, Union

from shared.consensus.pow import ProofOfWork
from node.wallet.core.tx_builder import TransactionBuilder


class MiningControlHandlers:
    """RPC handlers for advanced mining control."""

    def __init__(self, node: Any) -> None:
        self.node = node

    async def set_generate(self, generate: bool, threads: int = 1) -> Dict[str, Any]:
        """Start or stop background mining (regtest)."""
        if self.node.config.get("network") != "regtest":
            return {"error": "Mining only available on regtest"}

        if not self.node.miner:
            return {"error": "Miner not initialized"}

        if generate:
            mining_addr = (self.node.config.get("miningaddress") or "").strip()
            if not mining_addr:
                mining_addr = (self.node.miner.mining_address or "").strip()
            if not mining_addr:
                manager = getattr(self.node, "simple_wallet_manager", None)
                active_wallet = manager.get_active_wallet() if manager else None
                if active_wallet:
                    mining_addr = active_wallet.address
            if not mining_addr:
                return {"error": "Set mining address first (or activate wallet)"}
            try:
                TransactionBuilder(self.node.config.get("network", "mainnet"))._create_script_pubkey(mining_addr)
            except Exception:
                return {"error": "Invalid mining address"}
            self.node.config.set("miningaddress", mining_addr)
            self.node.miner.mining_address = mining_addr

            if self.node.miner.is_mining:
                return {"status": "already_mining"}

            await self.node.miner.start_mining(
                mining_addr, threads
            )
            return {
                "status": "started",
                "threads": threads,
                "address": mining_addr,
            }

        if not self.node.miner.is_mining:
            return {"status": "already_stopped"}

        await self.node.miner.stop_mining()
        return {
            "status": "stopped",
            "blocks_mined": self.node.miner.blocks_mined,
            "total_hashes": self.node.miner.total_hashes,
        }

    async def get_mining_status(self) -> Dict[str, Any]:
        """Return current mining status and chain tip info."""
        if not self.node.miner:
            return {"error": "Miner not initialized"}

        stats = self.node.miner.get_stats()

        best_height = self.node.chainstate.get_best_height()
        best_header = (
            self.node.chainstate.get_header(best_height)
            if best_height >= 0
            else None
        )

        return {
            "mining_enabled": self.node.config.get("mining", False),
            "is_mining": stats["mining"],
            "blocks_mined": stats["blocks_mined"],
            "total_hashes": stats["total_hashes"],
            "avg_hashrate": stats["avg_hashrate"],
            "uptime": stats["uptime"],
            "mining_address": stats["mining_address"],
            "current_height": best_height,
            "network_difficulty": self._get_difficulty(best_header.bits)
            if best_header
            else 1.0,
            "threads": self.node.config.get("mining_threads", 1),
        }

    async def set_mining_address(self, address: str) -> Dict[str, Any]:
        """Set mining reward address."""
        if not self.node.miner:
            return {"error": "Miner not initialized"}
        address = (address or "").strip()
        if not address:
            return {"error": "Address required"}
        try:
            TransactionBuilder(self.node.config.get("network", "mainnet"))._create_script_pubkey(address)
        except Exception:
            return {"error": "Invalid mining address"}

        old_address = self.node.miner.mining_address
        self.node.miner.mining_address = address
        self.node.config.set("miningaddress", address)

        block_assembler = getattr(self.node.miner, "block_assembler", None)
        if block_assembler is not None:
            block_assembler.coinbase_address = address

        return {
            "status": "updated",
            "old_address": old_address,
            "new_address": address,
        }

    async def get_mining_templates(self, count: int = 10) -> List[Dict[str, Any]]:
        """Return recent mining templates (placeholder; not stored yet)."""
        _ = count
        return []

    async def get_mining_workers(
        self,
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Stratum worker API is intentionally disabled in this release."""
        return {"error": "Stratum API disabled in this release"}

    async def set_mining_difficulty(self, difficulty: float) -> Dict[str, Any]:
        """Stratum share difficulty control is intentionally disabled."""
        _ = difficulty
        return {"error": "Stratum API disabled in this release"}

    def _get_difficulty(self, bits: int) -> float:
        pow_check = ProofOfWork(self.node.chainstate.params)
        return pow_check.calculate_difficulty(bits)
