"""Chain work calculation for proof of work."""

import math
from typing import List, Optional
from shared.core.block import BlockHeader
from shared.consensus.pow import ProofOfWork
from shared.consensus.params import ConsensusParams

class ChainWork:
    def __init__(self, params: ConsensusParams):
        self.params = params
        self.pow = ProofOfWork(params)

    def calculate_block_work(self, bits: int) -> int:
        target = self.pow.get_target(bits)
        max_target = self.params.pow_limit
        if target == 0:
            return 0
        return max_target // (target + 1)

    def calculate_block_work_from_header(self, header: BlockHeader) -> int:
        return self.calculate_block_work(header.bits)

    def calculate_chain_work(self, headers: List[BlockHeader]) -> int:
        total = 0
        for header in headers:
            total += self.calculate_block_work_from_header(header)
        return total

    def calculate_chain_work_from_bits(self, bits_list: List[int]) -> int:
        total = 0
        for bits in bits_list:
            total += self.calculate_block_work(bits)
        return total

    def compare_chain_work(self, work1: int, work2: int) -> int:
        if work1 < work2:
            return -1
        elif work1 > work2:
            return 1
        return 0

    def has_more_work(self, work1: int, work2: int) -> bool:
        return work1 > work2

    def get_work_difference(self, work1: int, work2: int) -> int:
        return abs(work1 - work2)

    def get_work_ratio(self, work1: int, work2: int) -> float:
        if work2 == 0:
            return float('inf')
        return work1 / work2

    def difficulty_to_work(self, difficulty: float) -> int:
        max_target = self.params.pow_limit
        target = max_target / difficulty
        return max_target // (int(target) + 1)

    def work_to_difficulty(self, work: int) -> float:
        max_target = self.params.pow_limit
        target = max_target // work - 1
        if target <= 0:
            return float('inf')
        return max_target / target

    def get_expected_time(self, work: int, hashrate: float) -> float:
        if hashrate <= 0:
            return float('inf')
        return work / hashrate

    def get_required_hashrate(self, work: int, target_time: int) -> float:
        if target_time <= 0:
            return float('inf')
        return work / target_time

    def is_better_chain(self, chain1_work: int, chain1_height: int,
                        chain2_work: int, chain2_height: int) -> bool:
        if chain1_work != chain2_work:
            return chain1_work > chain2_work
        return chain1_height > chain2_height
