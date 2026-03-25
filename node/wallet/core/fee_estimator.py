"""Fee estimation for wallet."""

import time
from typing import Dict, List
from collections import deque
from shared.utils.logging import get_logger

logger = get_logger()

class FeeEstimator:
    """Fee estimation for transactions."""
    
    def __init__(self):
        """Initialize fee estimator."""
        # Fee rate history (sat/vbyte)
        self.history: Dict[int, List[float]] = {}  # block height -> fee rates
        self.recent_fees: deque = deque(maxlen=1000)
        
        # Fee rate buckets
        self.buckets = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
        
        # Estimates
        self.estimates: Dict[str, float] = {
            'economy': 1,
            'normal': 5,
            'priority': 10
        }
    
    def add_transaction(self, fee: int, size: int, height: int) -> None:
        """Add transaction for fee estimation.
        
        Args:
            fee: Transaction fee in satoshis
            size: Transaction size in bytes
            height: Block height
        """
        fee_rate = fee / size if size > 0 else 0
        
        if height not in self.history:
            self.history[height] = []
        
        self.history[height].append(fee_rate)
        self.recent_fees.append(fee_rate)
        
        # Keep history limited
        if len(self.history) > 100:
            oldest = min(self.history.keys())
            del self.history[oldest]
    
    def update_estimates(self, current_height: int) -> None:
        """Update fee estimates.
        
        Args:
            current_height: Current block height
        """
        if not self.history:
            return
        
        # Get recent fee rates (last 100 blocks)
        recent_heights = sorted(self.history.keys(), reverse=True)[:100]
        recent_fees = []
        
        for height in recent_heights:
            recent_fees.extend(self.history[height])
        
        if not recent_fees:
            return
        
        recent_fees.sort()
        
        # Calculate percentiles
        self.estimates['economy'] = self._get_percentile(recent_fees, 25)  # 25th percentile
        self.estimates['normal'] = self._get_percentile(recent_fees, 50)   # 50th percentile
        self.estimates['priority'] = self._get_percentile(recent_fees, 90) # 90th percentile
        
        # Ensure minimums
        self.estimates['economy'] = max(1, self.estimates['economy'])
        self.estimates['normal'] = max(2, self.estimates['normal'])
        self.estimates['priority'] = max(5, self.estimates['priority'])
    
    def _get_percentile(self, data: List[float], percentile: int) -> float:
        """Get percentile from sorted data.
        
        Args:
            data: Sorted list of values
            percentile: Percentile (0-100)
        
        Returns:
            Value at percentile
        """
        if not data:
            return 0
        
        idx = int(len(data) * percentile / 100)
        return data[min(idx, len(data) - 1)]
    
    def get_fee_rate(self, confirmation_target: int = 6) -> float:
        """Get fee rate for confirmation target.
        
        Args:
            confirmation_target: Target blocks for confirmation
        
        Returns:
            Fee rate in sat/vbyte
        """
        if confirmation_target <= 2:
            return self.estimates['priority']
        elif confirmation_target <= 6:
            return self.estimates['normal']
        else:
            return self.estimates['economy']
    
    def get_fee(self, size: int, confirmation_target: int = 6) -> int:
        """Get fee for transaction.
        
        Args:
            size: Transaction size in bytes
            confirmation_target: Target blocks for confirmation
        
        Returns:
            Fee in satoshis
        """
        fee_rate = self.get_fee_rate(confirmation_target)
        return int(size * fee_rate)
    
    def get_smart_fee(self, size: int, confirmation_target: int = 6) -> int:
        """Get smart fee recommendation.
        
        Args:
            size: Transaction size in bytes
            confirmation_target: Target blocks for confirmation
        
        Returns:
            Recommended fee in satoshis
        """
        fee_rate = self.get_fee_rate(confirmation_target)
        
        # Add 10% buffer for mempool fluctuations
        fee_rate *= 1.1
        
        return int(size * fee_rate)
    
    def get_minimum_fee(self, size: int) -> int:
        """Get minimum relay fee.
        
        Args:
            size: Transaction size in bytes
        
        Returns:
            Minimum fee in satoshis
        """
        return size  # 1 sat/vbyte minimum
    
    def get_priority_fee(self, size: int) -> int:
        """Get priority fee.
        
        Args:
            size: Transaction size in bytes
        
        Returns:
            Priority fee in satoshis
        """
        return int(size * 10)  # 10 sat/vbyte
    
    def get_estimates(self) -> Dict[str, float]:
        """Get all fee estimates.
        
        Returns:
            Dictionary of fee estimates
        """
        self.update_estimates(int(time.time()) // 600)  # Approximate height
        return self.estimates.copy()
    
    def get_stats(self) -> Dict[str, object]:
        """Get fee estimator statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            'history_blocks': len(self.history),
            'recent_fees': len(self.recent_fees),
            'estimates': self.estimates,
            'avg_recent_fee': sum(self.recent_fees) / len(self.recent_fees) if self.recent_fees else 0
        }
