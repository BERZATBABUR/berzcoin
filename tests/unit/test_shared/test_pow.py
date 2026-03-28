"""Unit tests for Proof-of-Work validation and retarget behavior."""

import unittest

from shared.consensus.params import ConsensusParams
from shared.consensus.pow import ProofOfWork
from shared.core.block import BlockHeader


class TestPoW(unittest.TestCase):
    def test_block_header_pow_compares_hash_integer_to_target(self) -> None:
        h = BlockHeader()
        fake_hash = bytes.fromhex("00ff" + "00" * 30)
        h.hash = lambda: fake_hash  # type: ignore[method-assign]
        target_equal = int.from_bytes(fake_hash, "big")
        target_smaller = target_equal - 1
        self.assertTrue(h.is_valid_pow(target_equal))
        self.assertFalse(h.is_valid_pow(target_smaller))

    def test_get_target_is_capped_by_pow_limit(self) -> None:
        params = ConsensusParams.regtest()
        pow_check = ProofOfWork(params)
        # Very large compact target; should be clamped to pow_limit.
        huge_bits = 0x2200FFFF
        target = pow_check.get_target(huge_bits)
        self.assertEqual(target, params.pow_limit)

    def test_retarget_clamps_to_quarter_timespan_minimum(self) -> None:
        params = ConsensusParams.regtest()
        params.pow_target_spacing = 120
        params.pow_target_timespan = 1200  # interval = 10
        params.pow_no_retargeting = False
        pow_check = ProofOfWork(params)

        current_target = params.pow_limit // 1024
        bits = pow_check.get_bits(current_target)
        effective_current_target = pow_check.get_target(bits)
        first_ts = 1_700_000_000
        # Very fast interval -> should clamp to target_span/4.
        last_ts = first_ts + 1
        headers = [
            BlockHeader(bits=bits, timestamp=first_ts),
            *[BlockHeader(bits=bits, timestamp=first_ts + i) for i in range(1, 9)],
            BlockHeader(bits=bits, timestamp=last_ts),
        ]
        next_bits = pow_check.get_next_work_required(headers, height=9)
        expected = max(
            1,
            (effective_current_target * (params.pow_target_timespan // 4)) // params.pow_target_timespan,
        )
        self.assertEqual(next_bits, pow_check.get_bits(expected))

    def test_retarget_clamps_to_four_timespan_maximum(self) -> None:
        params = ConsensusParams.regtest()
        params.pow_target_spacing = 120
        params.pow_target_timespan = 1200  # interval = 10
        params.pow_no_retargeting = False
        pow_check = ProofOfWork(params)

        current_target = params.pow_limit // 4096
        bits = pow_check.get_bits(current_target)
        effective_current_target = pow_check.get_target(bits)
        first_ts = 1_700_000_000
        # Very slow interval -> should clamp to target_span*4.
        last_ts = first_ts + (params.pow_target_timespan * 100)
        headers = [
            BlockHeader(bits=bits, timestamp=first_ts),
            *[BlockHeader(bits=bits, timestamp=first_ts + i) for i in range(1, 9)],
            BlockHeader(bits=bits, timestamp=last_ts),
        ]
        next_bits = pow_check.get_next_work_required(headers, height=9)
        expected = min(
            params.pow_limit,
            (effective_current_target * (params.pow_target_timespan * 4)) // params.pow_target_timespan,
        )
        self.assertEqual(next_bits, pow_check.get_bits(expected))


if __name__ == "__main__":
    unittest.main()
