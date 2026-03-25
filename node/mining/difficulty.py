"""Difficulty calculation and adjustment."""

from typing import List

from shared.core.block import BlockHeader
from shared.consensus.params import ConsensusParams


class DifficultyCalculator:
    """Difficulty calculation utilities."""

    def __init__(self, params: ConsensusParams):
        self.params = params
        self.max_target = params.pow_limit
        self.target_spacing = params.pow_target_spacing
        self.target_timespan = params.pow_target_timespan
        self.retarget_interval = params.retarget_interval_blocks()

    def bits_to_difficulty(self, bits: int) -> float:
        target = self.bits_to_target(bits)
        if target <= 0:
            return float('inf')
        return self.max_target / target

    def difficulty_to_bits(self, difficulty: float) -> int:
        if difficulty <= 0:
            difficulty = 1e-12
        target = int(self.max_target / difficulty)
        return self.target_to_bits(target)

    def bits_to_target(self, bits: int) -> int:
        exponent = bits >> 24
        coefficient = bits & 0x007fffff

        if exponent <= 3:
            target = coefficient >> (8 * (3 - exponent))
        else:
            target = coefficient << (8 * (exponent - 3))

        return min(target, self.max_target)

    def target_to_bits(self, target: int) -> int:
        if target <= 0:
            target = 1

        if target > self.max_target:
            target = self.max_target

        target_bytes = target.to_bytes(32, 'big')

        first_nonzero = 0
        for i, byte in enumerate(target_bytes):
            if byte != 0:
                first_nonzero = i
                break
        else:
            return self.params.genesis_bits

        exponent = 32 - first_nonzero
        coefficient = int.from_bytes(target_bytes[first_nonzero:first_nonzero + 3], 'big')

        if coefficient >= 0x800000:
            coefficient >>= 8
            exponent += 1

        return (exponent << 24) | coefficient

    def calculate_work(self, bits: int) -> int:
        target = self.bits_to_target(bits)
        if target <= 0:
            return 0
        return self.max_target // (target + 1)

    def calculate_chain_work(self, headers: List[BlockHeader]) -> int:
        total = 0
        for header in headers:
            total += self.calculate_work(header.bits)
        return total

    def get_next_work_required(self, last_headers: List[BlockHeader], height: int) -> int:
        if self.params.pow_no_retargeting:
            return last_headers[-1].bits

        if (height + 1) % self.retarget_interval != 0:
            return last_headers[-1].bits

        first_header = last_headers[0]
        last_header = last_headers[-1]

        timespan = last_header.timestamp - first_header.timestamp

        min_timespan = self.target_timespan // 4
        max_timespan = self.target_timespan * 4
        timespan = max(min_timespan, min(timespan, max_timespan))

        current_target = self.bits_to_target(last_header.bits)
        new_target = current_target * timespan // self.target_timespan

        if new_target > self.max_target:
            new_target = self.max_target

        return self.target_to_bits(new_target)

    def get_expected_time(self, current_difficulty: float, hashrate: float) -> float:
        if hashrate <= 0:
            return float('inf')

        work = self.difficulty_to_work(current_difficulty)
        return work / hashrate

    def difficulty_to_work(self, difficulty: float) -> int:
        if difficulty <= 0:
            return 0
        target = int(self.max_target / difficulty)
        if target <= 0:
            return 0
        return self.max_target // (target + 1)

    def work_to_difficulty(self, work: int) -> float:
        if work <= 0:
            return float('inf')
        target = self.max_target // work - 1
        if target <= 0:
            return float('inf')
        return self.max_target / target

    def get_required_hashrate(self, target_time: int) -> float:
        if target_time <= 0:
            return 0.0
        return 0.0

    def get_difficulty_adjustment(self, last_headers: List[BlockHeader]) -> float:
        if len(last_headers) < 2:
            return 1.0

        first_header = last_headers[0]
        last_header = last_headers[-1]

        actual_timespan = last_header.timestamp - first_header.timestamp
        expected_timespan = self.target_timespan

        if actual_timespan <= 0:
            return 4.0

        adjustment = expected_timespan / actual_timespan

        return max(0.25, min(4.0, adjustment))

    def get_network_hashrate_estimate(self, blocks: int = 120) -> float:
        _ = blocks
        return 0.0

    def get_difficulty_string(self, bits: int) -> str:
        difficulty = self.bits_to_difficulty(bits)

        if difficulty >= 1e12:
            return f"{difficulty/1e12:.2f}T"
        if difficulty >= 1e9:
            return f"{difficulty/1e9:.2f}G"
        if difficulty >= 1e6:
            return f"{difficulty/1e6:.2f}M"
        if difficulty >= 1e3:
            return f"{difficulty/1e3:.2f}K"
        return f"{difficulty:.2f}"
