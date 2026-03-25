"""Health checks for BerzCoin node."""

import time
from typing import Any, Dict

from shared.utils.logging import get_logger

logger = get_logger()


class HealthChecker:
    """Check node health."""

    def __init__(self, node: Any):
        self.node = node

    async def check(self) -> Dict[str, Any]:
        checks = {
            "database": await self._check_database(),
            "chainstate": await self._check_chainstate(),
            "network": await self._check_network(),
            "mempool": await self._check_mempool(),
            "wallet": await self._check_wallet(),
        }
        overall = not any(c.get("status") == "unhealthy" for c in checks.values())
        return {
            "status": "healthy" if overall else "unhealthy",
            "timestamp": int(time.time()),
            "checks": checks,
        }

    async def _check_database(self) -> Dict[str, Any]:
        if not self.node.db:
            return {"status": "unhealthy", "message": "Database not initialized"}
        try:
            self.node.db.execute("SELECT 1")
            return {"status": "healthy", "message": "Database OK"}
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    async def _check_chainstate(self) -> Dict[str, Any]:
        if not self.node.chainstate:
            return {"status": "unhealthy", "message": "Chainstate not initialized"}
        try:
            best_height = self.node.chainstate.get_best_height()
            return {
                "status": "healthy",
                "message": f"Chain at height {best_height}",
                "height": best_height,
            }
        except Exception as e:
            return {"status": "unhealthy", "message": str(e)}

    async def _check_network(self) -> Dict[str, Any]:
        if not getattr(self.node, "connman", None):
            return {"status": "healthy", "message": "Network disabled"}
        peer_count = self.node.connman.get_connected_count()
        if peer_count == 0:
            return {"status": "warning", "message": "No connected peers"}
        return {
            "status": "healthy",
            "message": f"{peer_count} peers connected",
            "peers": peer_count,
        }

    async def _check_mempool(self) -> Dict[str, Any]:
        if not getattr(self.node, "mempool", None):
            return {"status": "healthy", "message": "Mempool disabled"}
        size = len(self.node.mempool.transactions)
        return {
            "status": "healthy",
            "message": f"{size} transactions in mempool",
            "size": size,
        }

    async def _check_wallet(self) -> Dict[str, Any]:
        if not getattr(self.node, "wallet", None):
            return {"status": "healthy", "message": "Wallet disabled"}
        if self.node.wallet.locked:
            return {"status": "warning", "message": "Wallet locked"}
        return {
            "status": "healthy",
            "message": "Wallet OK",
            "balance": self.node.wallet.get_balance(),
        }

    def is_ready(self) -> bool:
        if not self.node.db or not self.node.chainstate:
            return False
        if self.node.mode_manager.is_full_node():
            if self.node.chainstate.get_best_height() < 100:
                return False
        return True
