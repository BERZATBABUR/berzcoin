"""Mempool limits and eviction."""

import time
from typing import List, Set, Dict, Optional
from dataclasses import dataclass
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class MempoolLimits:
    max_size: int = 300_000_000
    max_weight: int = 1_500_000_000
    max_transactions: int = 50_000
    max_ancestors: int = 25
    max_descendants: int = 25
    max_orphans: int = 100
    min_fee_rate: int = 1000
    expiry_hours: int = 336

class MempoolLimitsManager:
    def __init__(self, limits: MempoolLimits = None):
        self.limits = limits or MempoolLimits()
        self.current_size = 0
        self.current_weight = 0
        self.current_count = 0

    def can_accept(self, size: int, weight: int, count: int) -> bool:
        if self.current_size + size > self.limits.max_size:
            return False
        if self.current_weight + weight > self.limits.max_weight:
            return False
        if self.current_count + count > self.limits.max_transactions:
            return False
        return True

    def add_transaction(self, size: int, weight: int) -> None:
        self.current_size += size
        self.current_weight += weight
        self.current_count += 1

    def remove_transaction(self, size: int, weight: int) -> None:
        self.current_size -= size
        self.current_weight -= weight
        self.current_count -= 1

    def get_usage(self) -> Dict[str, int]:
        return {
            'size': self.current_size,
            'weight': self.current_weight,
            'count': self.current_count,
            'size_percent': (self.current_size / self.limits.max_size) * 100,
            'weight_percent': (self.current_weight / self.limits.max_weight) * 100,
            'count_percent': (self.current_count / self.limits.max_transactions) * 100,
        }

    def is_above_limit(self) -> bool:
        return (
            self.current_size > self.limits.max_size
            or self.current_weight > self.limits.max_weight
            or self.current_count > self.limits.max_transactions
        )

    def get_eviction_candidates(self, transactions: Dict[str, any]) -> List[str]:
        candidates = []
        tx_list = [(txid, entry) for txid, entry in transactions.items()]
        tx_list.sort(key=lambda x: x[1].fee_rate)
        current_time = time.time()
        for txid, entry in tx_list:
            age_hours = (current_time - entry.time_added) / 3600
            if age_hours > self.limits.expiry_hours:
                candidates.append(txid)
            elif len(candidates) < 100:
                candidates.append(txid)
        return candidates

    def reset(self) -> None:
        self.current_size = 0
        self.current_weight = 0
        self.current_count = 0

    def get_stats(self) -> Dict[str, any]:
        return {
            'max_size': self.limits.max_size,
            'max_weight': self.limits.max_weight,
            'max_transactions': self.limits.max_transactions,
            'current': self.get_usage(),
            'expiry_hours': self.limits.expiry_hours,
            'min_fee_rate': self.limits.min_fee_rate,
        }

class AncestorDescendantLimits:
    def __init__(self, max_ancestors: int = 25, max_descendants: int = 25):
        self.max_ancestors = max_ancestors
        self.max_descendants = max_descendants

    def can_accept(self, ancestors: int, descendants: int) -> bool:
        return ancestors <= self.max_ancestors and descendants <= self.max_descendants

    def get_ancestor_limit(self) -> int:
        return self.max_ancestors

    def get_descendant_limit(self) -> int:
        return self.max_descendants
