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
            "sync": await self._check_sync(),
        }
        unhealthy = [name for name, c in checks.items() if c.get("status") == "unhealthy"]
        warning = [name for name, c in checks.items() if c.get("status") == "warning"]
        if unhealthy:
            overall = "unhealthy"
        elif warning:
            overall = "degraded"
        else:
            overall = "healthy"
        return {
            "status": overall,
            "timestamp": int(time.time()),
            "checks": checks,
            "summary": {
                "unhealthy_checks": unhealthy,
                "warning_checks": warning,
                "unhealthy_count": len(unhealthy),
                "warning_count": len(warning),
            },
            "ready": self.is_ready(),
        }

    async def _check_database(self) -> Dict[str, Any]:
        if not self.node.db:
            return {"status": "unhealthy", "message": "Database not initialized"}
        try:
            self.node.db.execute("SELECT 1")
            consistency = {}
            if hasattr(self.node.db, "check_consistency"):
                consistency = self.node.db.check_consistency(quick=True)
                if not consistency.get("integrity_ok", False):
                    return {
                        "status": "unhealthy",
                        "message": "Database integrity check failed",
                        "consistency": consistency,
                    }
                if not consistency.get("foreign_keys_ok", False):
                    return {
                        "status": "unhealthy",
                        "message": "Database foreign key check failed",
                        "consistency": consistency,
                    }
            return {
                "status": "healthy",
                "message": "Database OK",
                "consistency": consistency or None,
            }
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
        min_peers_warn = int(getattr(self.node, "config", {}).get("health_min_peers_warn", 1))
        if peer_count < max(1, min_peers_warn):
            return {"status": "warning", "message": f"Low peer count: {peer_count}", "peers": peer_count}
        return {
            "status": "healthy",
            "message": f"{peer_count} peers connected",
            "peers": peer_count,
        }

    async def _check_mempool(self) -> Dict[str, Any]:
        if not getattr(self.node, "mempool", None):
            return {"status": "healthy", "message": "Mempool disabled"}
        size = len(self.node.mempool.transactions)
        warn_limit = int(getattr(self.node, "config", {}).get("health_max_mempool_txs_warn", 200000))
        if size > max(1, warn_limit):
            return {
                "status": "warning",
                "message": f"Mempool size high: {size}",
                "size": size,
            }
        return {
            "status": "healthy",
            "message": f"{size} transactions in mempool",
            "size": size,
        }

    async def _check_wallet(self) -> Dict[str, Any]:
        manager = getattr(self.node, "simple_wallet_manager", None)
        if not manager:
            return {"status": "healthy", "message": "Wallet disabled"}
        active = manager.get_active_wallet()
        if not active:
            return {"status": "warning", "message": "No active private-key wallet"}
        balance = 0
        if getattr(self.node, "chainstate", None):
            balance = self.node.chainstate.get_balance(active.address)
        return {
            "status": "healthy",
            "message": "Wallet OK",
            "address": active.address,
            "balance": balance,
        }

    async def _check_sync(self) -> Dict[str, Any]:
        connman = getattr(self.node, "connman", None)
        chainstate = getattr(self.node, "chainstate", None)
        if not connman or not chainstate:
            return {"status": "healthy", "message": "Sync check unavailable"}
        best_peer = connman.get_best_height_peer()
        if not best_peer:
            return {"status": "warning", "message": "No sync peer available"}
        local = chainstate.get_best_height()
        remote = int(best_peer.peer_height)
        lag = max(0, remote - local)
        cfg = getattr(self.node, "config", {})
        warn_lag = int(cfg.get("health_sync_lag_warn_blocks", 24))
        critical_lag = int(cfg.get("health_sync_lag_critical_blocks", 144))
        if lag > max(warn_lag, critical_lag):
            return {"status": "unhealthy", "message": f"Node is {lag} blocks behind peer", "lag": lag}
        if lag > warn_lag:
            return {"status": "warning", "message": f"Node is {lag} blocks behind peer", "lag": lag}
        return {"status": "healthy", "message": f"Sync lag {lag} blocks", "lag": lag}

    def is_ready(self) -> bool:
        if not self.node.db or not self.node.chainstate:
            return False
        if self.node.mode_manager.is_full_node():
            if self.node.chainstate.get_best_height() < 100:
                return False
        return True
