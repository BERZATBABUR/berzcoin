"""Prometheus metrics for BerzCoin."""

import time
from typing import Dict, Any
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from shared.utils.logging import get_logger

logger = get_logger()

# Blockchain metrics
block_height = Gauge('berzcoin_block_height', 'Current block height')
block_processing_time = Histogram('berzcoin_block_processing_seconds', 'Block processing time')

# Mempool metrics
mempool_size = Gauge('berzcoin_mempool_size', 'Number of transactions in mempool')
mempool_bytes = Gauge('berzcoin_mempool_bytes', 'Total mempool size in bytes')

# Network metrics
peers_connected = Gauge('berzcoin_peers_connected', 'Number of connected peers')
bytes_received = Counter('berzcoin_bytes_received', 'Bytes received over P2P')
bytes_sent = Counter('berzcoin_bytes_sent', 'Bytes sent over P2P')

# Wallet metrics
wallet_balance = Gauge('berzcoin_wallet_balance', 'Wallet balance in satoshis')
wallet_tx_count = Counter('berzcoin_wallet_transactions', 'Number of wallet transactions')

# Mining metrics
mining_hashrate = Gauge('berzcoin_mining_hashrate', 'Current mining hashrate')
blocks_mined = Counter('berzcoin_blocks_mined', 'Blocks mined by this node')


class MetricsCollector:
    """Collect and expose metrics."""
    
    def __init__(self, node, port: int = 9332):
        """Initialize metrics collector."""
        self.node = node
        self.port = port
        
        # Start Prometheus HTTP server
        start_http_server(port)
        logger.info(f"Metrics server started on port {port}")
    
    def update(self):
        """Update all metrics."""
        if getattr(self.node, 'chainstate', None):
            try:
                block_height.set(self.node.chainstate.get_best_height())
            except Exception:
                pass
        
        if getattr(self.node, 'mempool', None):
            try:
                stats = self.node.mempool.get_stats()
                mempool_size.set(stats.get('size', 0))
                mempool_bytes.set(stats.get('total_size', 0))
            except Exception:
                pass
        
        if getattr(self.node, 'connman', None):
            try:
                peers_connected.set(len(self.node.connman.peers))
            except Exception:
                pass
        
        if getattr(self.node, 'wallet', None):
            try:
                wallet_balance.set(self.node.wallet.get_balance())
            except Exception:
                pass
    
    def record_block_processed(self, processing_time: float):
        """Record block processing time."""
        block_processing_time.observe(processing_time)
    
    def record_bytes_sent(self, bytes_count: int):
        """Record bytes sent."""
        bytes_sent.inc(bytes_count)
    
    def record_bytes_received(self, bytes_count: int):
        """Record bytes received."""
        bytes_received.inc(bytes_count)
