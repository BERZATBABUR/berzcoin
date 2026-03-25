"""Proof of Work validation and difficulty adjustment."""

import math
from typing import List
from .params import ConsensusParams
from ..core.block import BlockHeader

class ProofOfWork:
    """Proof of Work validation and difficulty adjustment."""

    def __init__(self, params: ConsensusParams):
        self.params = params

    def validate(self, header: BlockHeader) -> bool:
        target = self.get_target(header.bits)
        return header.is_valid_pow(target)

    def get_target(self, bits: int) -> int:
        exponent = bits >> 24
        coefficient = bits & 0x007fffff
        if exponent <= 3:
            target = coefficient >> (8 * (3 - exponent))
        else:
            target = coefficient << (8 * (exponent - 3))
        if target < 0 or target > self.params.pow_limit:
            target = self.params.pow_limit
        return target

    def get_bits(self, target: int) -> int:
        target_bytes = target.to_bytes(32, 'big')
        for i, byte in enumerate(target_bytes):
            if byte != 0:
                break
        exponent = 32 - i
        coefficient = int.from_bytes(target_bytes[i:i+3], 'big')
        if coefficient >= 0x800000:
            coefficient >>= 8
            exponent += 1
        return (exponent << 24) | coefficient

    def calculate_difficulty(self, bits: int) -> float:
        target = self.get_target(bits)
        max_target = self.params.pow_limit
        return max_target / target

    def get_next_work_required(self, last_headers: List[BlockHeader], height: int) -> int:
        """Next ``bits`` for the block mined **after** chain tip ``height`` (Bitcoin-style).

        Retargeting runs when the **next** block height is a multiple of
        ``retarget_interval_blocks()`` (mainnet: every 2016 blocks). Regtest /
        testnet set ``pow_no_retargeting`` to keep the previous ``bits``.
        """
        if not last_headers:
            return self.params.genesis_bits

        # Regtest / test profiles can disable retargeting entirely.
        if self.params.pow_no_retargeting:
            return last_headers[-1].bits

        interval = self.params.retarget_interval_blocks()
        # Tip height is ``height``; next block will be ``height + 1``.
        if (height + 1) % interval != 0:
            return last_headers[-1].bits

        # Retarget boundary: compute based on the prior interval timestamps.
        first_header = last_headers[0]
        last_header = last_headers[-1]

        timespan = int(last_header.timestamp) - int(first_header.timestamp)
        target_span = int(self.params.pow_target_timespan)

        # Clamp to 0.25x - 4x target timespan.
        min_timespan = max(1, target_span // 4)
        max_timespan = max(1, target_span * 4)
        if timespan < min_timespan:
            timespan = min_timespan
        elif timespan > max_timespan:
            timespan = max_timespan

        current_target = int(self.get_target(last_header.bits))
        new_target = (current_target * timespan) // max(1, target_span)

        # Cap at pow limit; also avoid a zero/negative target.
        if new_target > int(self.params.pow_limit):
            new_target = int(self.params.pow_limit)
        if new_target < 1:
            new_target = 1

        return self.get_bits(int(new_target))

    def mine(self, header: BlockHeader, max_nonce: int = 2**32) -> bool:
        target = self.get_target(header.bits)
        for nonce in range(max_nonce):
            header.nonce = nonce
            if header.is_valid_pow(target):
                return True
        return False
