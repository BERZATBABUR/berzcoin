"""Fee calculation and estimation."""

import time
import bisect
from typing import List, Dict, Tuple, Optional
from collections import deque
from shared.utils.logging import get_logger

logger = get_logger()

class FeeCalculator:
    def __init__(self):
        self.history: Dict[int, List[float]] = {}
        self.rolling_average: deque = deque(maxlen=100)
        self.buckets = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
        self.estimates: Dict[int, float] = {}

    def add_transaction(self, fee: int, size: int, height: int) -> None:
        fee_rate = fee / size
        if height not in self.history:
            self.history[height] = []
        self.history[height].append(fee_rate)
        self.rolling_average.append(fee_rate)

    def get_fee_estimate(self, target_blocks: int = 6) -> float:
        if not self.history:
            return self.buckets[0]
        recent_heights = sorted(self.history.keys(), reverse=True)[:target_blocks * 2]
        recent_fees = []
        for height in recent_heights:
            recent_fees.extend(self.history[height])
        if not recent_fees:
            return self.buckets[0]
        percentile = min(90, 50 + target_blocks * 5)
        recent_fees.sort()
        idx = int(len(recent_fees) * percentile / 100)
        return recent_fees[min(idx, len(recent_fees) - 1)]

    def get_fee_rate(self, fee: int, size: int) -> float:
        return fee / size if size > 0 else 0

    def get_required_fee(self, size: int, target_blocks: int = 6) -> int:
        fee_rate = self.get_fee_estimate(target_blocks)
        return int(size * fee_rate)

    def get_smart_fee(self, size: int, confirmation_target: int = 6) -> int:
        fee_rate = self.get_fee_estimate(confirmation_target)
        fee_rate *= 1.1
        return int(size * fee_rate)

    def get_minimum_fee(self, size: int) -> int:
        return size

    def get_priority_fee(self, size: int) -> int:
        return size * 10

    def get_fee_buckets(self) -> List[float]:
        return self.buckets.copy()

    def get_fee_estimates(self) -> Dict[int, float]:
        estimates = {}
        for target in [1, 2, 3, 6, 12, 24, 48, 72]:
            estimates[target] = self.get_fee_estimate(target)
        return estimates

    def clear_history(self, max_age: int = 1000) -> None:
        if len(self.history) > max_age:
            old_heights = sorted(self.history.keys())[:-max_age]
            for height in old_heights:
                del self.history[height]

    def get_stats(self) -> Dict:
        return {
            'history_size': len(self.history),
            'rolling_average': sum(self.rolling_average) / len(self.rolling_average) if self.rolling_average else 0,
            'estimates': self.get_fee_estimates(),
        }

class FeeEstimator:
    def __init__(self, fee_calculator: FeeCalculator = None):
        self.calculator = fee_calculator or FeeCalculator()
        self.estimates: Dict[str, int] = {}

    def update_estimates(self) -> None:
        self.estimates['economy'] = self.calculator.get_required_fee(1000, 12)
        self.estimates['normal'] = self.calculator.get_required_fee(1000, 6)
        self.estimates['priority'] = self.calculator.get_required_fee(1000, 2)

    def get_fee(self, size: int, mode: str = 'normal') -> int:
        self.update_estimates()
        if mode == 'economy':
            return self.calculator.get_minimum_fee(size)
        elif mode == 'priority':
            return self.calculator.get_priority_fee(size)
        return self.calculator.get_smart_fee(size, 6)

    def get_fee_rate(self, mode: str = 'normal') -> float:
        self.update_estimates()
        if mode == 'economy':
            return 1
        elif mode == 'priority':
            return 10
        return self.calculator.get_fee_estimate(6)
