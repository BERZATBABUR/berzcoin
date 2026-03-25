"""Block subsidy calculation."""

from .params import ConsensusParams

def get_block_subsidy(height: int, params: ConsensusParams) -> int:
    """Calculate block subsidy for given height."""
    halvings = height // params.subsidy_halving_interval
    if halvings >= 64:
        return 0
    subsidy = params.initial_subsidy
    for _ in range(halvings):
        subsidy //= 2
    return subsidy

def get_total_supply(height: int, params: ConsensusParams) -> int:
    """Calculate total supply up to given height."""
    total = 0
    current_height = 0
    while current_height <= height:
        subsidy = get_block_subsidy(current_height, params)
        next_halving = ((current_height // params.subsidy_halving_interval) + 1) * params.subsidy_halving_interval
        blocks = min(next_halving, height + 1) - current_height
        total += subsidy * blocks
        current_height = next_halving
    return total

def get_max_supply(params: ConsensusParams) -> int:
    """Calculate maximum supply."""
    total = 0
    subsidy = params.initial_subsidy
    while subsidy > 0:
        total += subsidy * params.subsidy_halving_interval
        subsidy //= 2
    return total

def get_subsidy_for_block(height: int, params: ConsensusParams) -> int:
    """Alias for get_block_subsidy."""
    return get_block_subsidy(height, params)
