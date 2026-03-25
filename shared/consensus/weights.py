"""Block weight calculation for SegWit."""

from typing import Union
from ..core.block import Block
from ..core.transaction import Transaction

WITNESS_SCALE_FACTOR = 4
BASE_SIZE_MULTIPLIER = 3

def calculate_transaction_weight(tx: Transaction) -> int:
    base_size = len(tx.serialize(include_witness=False))
    total_size = len(tx.serialize(include_witness=True))
    weight = base_size * BASE_SIZE_MULTIPLIER + total_size
    return weight

def calculate_transaction_vsize(tx: Transaction) -> float:
    return calculate_transaction_weight(tx) / WITNESS_SCALE_FACTOR

def calculate_block_weight(block: Block) -> int:
    total_weight = 0
    for tx in block.transactions:
        total_weight += calculate_transaction_weight(tx)
    return total_weight

def calculate_block_vsize(block: Block) -> float:
    return calculate_block_weight(block) / WITNESS_SCALE_FACTOR

def calculate_base_block_size(block: Block) -> int:
    return len(block.serialize(include_witness=False))

def calculate_total_block_size(block: Block) -> int:
    return len(block.serialize(include_witness=True))

def is_within_weight_limit(block: Block, max_weight: int) -> bool:
    return calculate_block_weight(block) <= max_weight

def is_within_size_limit(block: Block, max_size: int) -> bool:
    return calculate_base_block_size(block) <= max_size
