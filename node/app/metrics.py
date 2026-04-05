"""Metrics collection for BerzCoin node."""

import time
from typing import Any, Dict

from shared.utils.logging import get_logger

logger = get_logger()

try:
    import psutil
except ImportError:
    psutil = None


class MetricsCollector:
    """Collect node metrics."""

    def __init__(self, node: Any):
        self.node = node
        self.start_time = time.time()
        self.blocks_processed = 0
        self.txs_processed = 0

    def _sync_lag(self) -> int:
        chainstate = getattr(self.node, "chainstate", None)
        connman = getattr(self.node, "connman", None)
        if not chainstate or not connman:
            return 0
        best_peer = connman.get_best_height_peer()
        if not best_peer:
            return 0
        local_height = int(chainstate.get_best_height())
        return max(0, int(best_peer.peer_height) - local_height)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _sync_pending_requests(self) -> int:
        sync = getattr(self.node, "block_sync", None)
        if sync is None:
            return 0
        return self._safe_int(len(getattr(sync, "_pending_block_requests", {})))

    def _orphan_blocks(self) -> int:
        sync = getattr(self.node, "block_sync", None)
        if sync is None or not getattr(sync, "orphanage", None):
            return 0
        try:
            return int(sync.orphanage.size())
        except Exception:
            return 0

    def _utxo_stats(self) -> Dict[str, int]:
        store = getattr(self.node, "utxo_store", None)
        if not store:
            return {"count": 0, "total_value": 0}
        try:
            return {
                "count": int(store.get_utxo_count()),
                "total_value": int(store.get_total_value()),
            }
        except Exception as e:
            logger.debug("UTXO metrics unavailable: %s", e)
            return {"count": 0, "total_value": 0}

    def _db_size_bytes(self) -> int:
        db = getattr(self.node, "db", None)
        if not db or not hasattr(db, "get_size"):
            return 0
        try:
            return int(db.get_size())
        except Exception:
            return 0

    def _slo(self) -> Dict[str, Any]:
        lag = self._sync_lag()
        cfg = getattr(self.node, "config", {})
        lag_budget = self._safe_int(cfg.get("health_sync_lag_warn_blocks", 24), 24)
        ready = False
        checker = getattr(self.node, "health_checker", None)
        if checker is not None:
            try:
                ready = bool(checker.is_ready())
            except Exception:
                ready = False
        return {
            "ready": ready,
            "sync_lag_blocks": lag,
            "sync_lag_budget_blocks": lag_budget,
            "sync_lag_slo_ok": lag <= max(0, lag_budget),
        }

    def get_metrics(self) -> Dict[str, Any]:
        sys_metrics: Dict[str, Any] = {}
        if psutil:
            try:
                process = psutil.Process()
                memory = process.memory_info()
                sys_metrics = {
                    "cpu_percent": process.cpu_percent(interval=0.1),
                    "memory_rss": memory.rss,
                    "memory_vms": memory.vms,
                    "memory_percent": process.memory_percent(),
                }
            except (psutil.Error, OSError) as e:
                logger.debug("psutil metrics unavailable: %s", e)
                sys_metrics = {"error": str(e)}
        else:
            sys_metrics = {"error": "psutil not installed"}

        best_height = (
            self.node.chainstate.get_best_height() if getattr(self.node, "chainstate", None) else 0
        )
        peer_count = (
            self.node.connman.get_connected_count()
            if getattr(self.node, "connman", None)
            else 0
        )
        mempool = getattr(self.node, "mempool", None)
        mempool_size = len(mempool.transactions) if mempool else 0
        mempool_weight = self._safe_int(getattr(mempool, "total_weight", 0), 0) if mempool else 0
        mempool_policy = {
            "min_fee_floor_rate": float(getattr(mempool, "min_fee_floor_rate", 0.0)) if mempool else 0.0,
            "reject_reason_counts": dict(getattr(mempool, "reject_reason_counts", {})) if mempool else {},
            "eviction_reason_counts": dict(getattr(mempool, "eviction_reason_counts", {})) if mempool else {},
        }
        utxo = self._utxo_stats()

        return {
            "system": sys_metrics,
            "node": {
                "uptime": time.time() - self.start_time,
                "best_height": best_height,
                "peers": peer_count,
                "mempool_size": mempool_size,
                "mempool_weight": mempool_weight,
                "mempool_min_fee_floor_rate": float(mempool_policy["min_fee_floor_rate"]),
                "blocks_processed": self.blocks_processed,
                "txs_processed": self.txs_processed,
                "sync_lag_blocks": self._sync_lag(),
                "pending_block_requests": self._sync_pending_requests(),
                "orphan_blocks": self._orphan_blocks(),
            },
            "mempool_policy": mempool_policy,
            "chainstate": {
                "utxo_count": utxo["count"],
                "utxo_total_value": utxo["total_value"],
                "db_size_bytes": self._db_size_bytes(),
            },
            "slo": self._slo(),
            "network": {
                "bytes_sent": 0,
                "bytes_received": 0,
                "messages_sent": 0,
                "messages_received": 0,
            },
        }

    def to_prometheus(self) -> str:
        """Export selected metrics in Prometheus text format."""
        metrics = self.get_metrics()
        lines = [
            "# HELP berzcoin_uptime_seconds Node uptime in seconds",
            "# TYPE berzcoin_uptime_seconds gauge",
            f"berzcoin_uptime_seconds {float(metrics['node']['uptime']):.6f}",
            "# HELP berzcoin_best_height Best known chain height",
            "# TYPE berzcoin_best_height gauge",
            f"berzcoin_best_height {int(metrics['node']['best_height'])}",
            "# HELP berzcoin_peer_count Connected peer count",
            "# TYPE berzcoin_peer_count gauge",
            f"berzcoin_peer_count {int(metrics['node']['peers'])}",
            "# HELP berzcoin_mempool_size Number of transactions in mempool",
            "# TYPE berzcoin_mempool_size gauge",
            f"berzcoin_mempool_size {int(metrics['node']['mempool_size'])}",
            "# HELP berzcoin_mempool_weight Mempool weight in weight units",
            "# TYPE berzcoin_mempool_weight gauge",
            f"berzcoin_mempool_weight {int(metrics['node']['mempool_weight'])}",
            "# HELP berzcoin_mempool_min_fee_floor_rate Current mempool min fee floor in sat/vB",
            "# TYPE berzcoin_mempool_min_fee_floor_rate gauge",
            f"berzcoin_mempool_min_fee_floor_rate {float(metrics['node']['mempool_min_fee_floor_rate']):.8f}",
            "# HELP berzcoin_sync_lag_blocks Best peer minus local height",
            "# TYPE berzcoin_sync_lag_blocks gauge",
            f"berzcoin_sync_lag_blocks {int(metrics['node']['sync_lag_blocks'])}",
            "# HELP berzcoin_pending_block_requests Number of in-flight block requests",
            "# TYPE berzcoin_pending_block_requests gauge",
            f"berzcoin_pending_block_requests {int(metrics['node']['pending_block_requests'])}",
            "# HELP berzcoin_orphan_blocks Number of orphan blocks tracked",
            "# TYPE berzcoin_orphan_blocks gauge",
            f"berzcoin_orphan_blocks {int(metrics['node']['orphan_blocks'])}",
            "# HELP berzcoin_chainstate_db_size_bytes SQLite chainstate DB size",
            "# TYPE berzcoin_chainstate_db_size_bytes gauge",
            f"berzcoin_chainstate_db_size_bytes {int(metrics['chainstate']['db_size_bytes'])}",
            "# HELP berzcoin_utxo_count Number of UTXOs in chainstate",
            "# TYPE berzcoin_utxo_count gauge",
            f"berzcoin_utxo_count {int(metrics['chainstate']['utxo_count'])}",
            "# HELP berzcoin_utxo_total_value Sum of UTXO values in satoshis",
            "# TYPE berzcoin_utxo_total_value gauge",
            f"berzcoin_utxo_total_value {int(metrics['chainstate']['utxo_total_value'])}",
            "# HELP berzcoin_readiness_slo Readiness gate status (1=ready)",
            "# TYPE berzcoin_readiness_slo gauge",
            f"berzcoin_readiness_slo {1 if metrics['slo']['ready'] else 0}",
            "# HELP berzcoin_sync_lag_slo Sync lag within configured budget (1=ok)",
            "# TYPE berzcoin_sync_lag_slo gauge",
            f"berzcoin_sync_lag_slo {1 if metrics['slo']['sync_lag_slo_ok'] else 0}",
            "# HELP berzcoin_blocks_processed_total Blocks processed since start",
            "# TYPE berzcoin_blocks_processed_total counter",
            f"berzcoin_blocks_processed_total {int(metrics['node']['blocks_processed'])}",
            "# HELP berzcoin_txs_processed_total Transactions processed since start",
            "# TYPE berzcoin_txs_processed_total counter",
            f"berzcoin_txs_processed_total {int(metrics['node']['txs_processed'])}",
        ]
        reject_counts = metrics.get("mempool_policy", {}).get("reject_reason_counts", {}) or {}
        if reject_counts:
            lines.extend(
                [
                    "# HELP berzcoin_mempool_reject_reason_total Count of mempool rejections by reason",
                    "# TYPE berzcoin_mempool_reject_reason_total counter",
                ]
            )
            for reason, count in sorted(reject_counts.items()):
                safe_reason = str(reason).replace("\\", "_").replace('"', "_")
                lines.append(
                    f'berzcoin_mempool_reject_reason_total{{reason="{safe_reason}"}} {int(count)}'
                )
        eviction_counts = metrics.get("mempool_policy", {}).get("eviction_reason_counts", {}) or {}
        if eviction_counts:
            lines.extend(
                [
                    "# HELP berzcoin_mempool_eviction_reason_total Count of mempool evictions by reason",
                    "# TYPE berzcoin_mempool_eviction_reason_total counter",
                ]
            )
            for reason, count in sorted(eviction_counts.items()):
                safe_reason = str(reason).replace("\\", "_").replace('"', "_")
                lines.append(
                    f'berzcoin_mempool_eviction_reason_total{{reason="{safe_reason}"}} {int(count)}'
                )
        return "\n".join(lines) + "\n"

    def record_block(self, block: Any) -> None:
        self.blocks_processed += 1
        self.txs_processed += len(block.transactions)

    def get_rate(self) -> Dict[str, float]:
        uptime = time.time() - self.start_time
        if uptime <= 0:
            return {
                "blocks_per_second": 0.0,
                "blocks_per_hour": 0.0,
                "txs_per_second": 0.0,
                "txs_per_hour": 0.0,
            }
        bps = self.blocks_processed / uptime
        return {
            "blocks_per_second": bps,
            "blocks_per_hour": bps * 3600.0,
            "txs_per_second": self.txs_processed / uptime,
            "txs_per_hour": (self.txs_processed / uptime) * 3600.0,
        }

    def reset(self) -> None:
        self.start_time = time.time()
        self.blocks_processed = 0
        self.txs_processed = 0
