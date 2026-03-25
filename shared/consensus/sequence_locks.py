"""Sequence lock validation (BIP68)."""

from typing import List, Optional
from ..core.transaction import Transaction
from ..core.block import BlockHeader

class SequenceLock:
    """Sequence lock for transaction inputs (BIP68)."""

    def __init__(self, min_height: int = 0, min_time: int = 0):
        self.min_height = min_height
        self.min_time = min_time

    def is_satisfied(self, height: int, time: int) -> bool:
        return height >= self.min_height and time >= self.min_time

    def __repr__(self) -> str:
        return f"SequenceLock(height={self.min_height}, time={self.min_time})"

def calculate_sequence_lock(tx: Transaction, inputs_height: List[int], inputs_time: List[int]) -> SequenceLock:
    min_height = 0
    min_time = 0

    for i, txin in enumerate(tx.vin):
        if txin.sequence == 0xffffffff:
            continue
        if txin.sequence & 0x80000000 == 0:
            continue

        relative_lock = txin.sequence & 0x0000ffff

        if txin.sequence & 0x00400000:
            lock_time = inputs_time[i] + (relative_lock << 9)
            min_time = max(min_time, lock_time)
        else:
            lock_height = inputs_height[i] + relative_lock
            min_height = max(min_height, lock_height)

    return SequenceLock(min_height, min_time)

def is_sequence_lock_satisfied(tx: Transaction, inputs_height: List[int], inputs_time: List[int],
                               block_height: int, block_time: int) -> bool:
    lock = calculate_sequence_lock(tx, inputs_height, inputs_time)
    return lock.is_satisfied(block_height, block_time)
