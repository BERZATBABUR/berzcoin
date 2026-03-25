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
            self.node.chainstate.get_best_height() if self.node.chainstate else 0
        )
        peer_count = (
            self.node.connman.get_connected_count()
            if getattr(self.node, "connman", None)
            else 0
        )
        mempool = getattr(self.node, "mempool", None)
        mempool_size = len(mempool.transactions) if mempool else 0

        return {
            "system": sys_metrics,
            "node": {
                "uptime": time.time() - self.start_time,
                "best_height": best_height,
                "peers": peer_count,
                "mempool_size": mempool_size,
                "blocks_processed": self.blocks_processed,
                "txs_processed": self.txs_processed,
            },
            "network": {
                "bytes_sent": 0,
                "bytes_received": 0,
                "messages_sent": 0,
                "messages_received": 0,
            },
        }

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
