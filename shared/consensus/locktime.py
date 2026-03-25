"""Locktime validation for transactions."""

from typing import Optional
from ..core.transaction import Transaction
from ..core.block import BlockHeader
from ..utils.time import current_time

class LocktimeValidator:
    """Validate transaction locktime."""

    def __init__(self, block_height: int, block_time: int):
        self.block_height = block_height
        self.block_time = block_time

    def is_final(self, tx: Transaction, height: int = None, time: int = None) -> bool:
        height = height if height is not None else self.block_height
        time = time if time is not None else self.block_time

        if tx.locktime == 0:
            return True

        is_block_height = tx.locktime < 500000000
        if is_block_height:
            return tx.locktime <= height
        return tx.locktime <= time

    def is_final_input(self, tx: Transaction, input_index: int, height: int = None, time: int = None) -> bool:
        if input_index >= len(tx.vin):
            return True

        sequence = tx.vin[input_index].sequence
        if sequence == 0xffffffff:
            return True

        height = height if height is not None else self.block_height
        time = time if time is not None else self.block_time

        if sequence & 0x80000000 == 0:
            return False

        is_block_height = tx.locktime < 500000000
        if is_block_height:
            return sequence & 0xffff <= height
        return (sequence & 0xffff) << 9 <= time

    def can_be_in_block(self, tx: Transaction, height: int = None, time: int = None) -> bool:
        if not self.is_final(tx, height, time):
            return False
        for i in range(len(tx.vin)):
            if not self.is_final_input(tx, i, height, time):
                return False
        return True

def is_locktime_valid(tx: Transaction, block_height: int, block_time: int) -> bool:
    """Check if locktime is valid for block."""
    validator = LocktimeValidator(block_height, block_time)
    return validator.can_be_in_block(tx)
