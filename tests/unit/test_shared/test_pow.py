"""Unit tests for Proof-of-Work validation and retarget behavior."""

import hashlib
import unittest

from shared.consensus.params import ConsensusParams
from shared.consensus.pow import ProofOfWork
from shared.core.block import BlockHeader


class TestPoW(unittest.TestCase):
    def test_header_serialization_and_hash_endian_regression(self) -> None:
        h = BlockHeader(
            version=2,
            prev_block_hash=bytes.fromhex("11" * 32),
            merkle_root=bytes.fromhex("22" * 32),
            timestamp=0x12345678,
            bits=0x1D00FFFF,
            nonce=0x01020304,
        )
        expected_serialized_hex = (
            "02000000"
            + ("11" * 32)
            + ("22" * 32)
            + "78563412"
            + "ffff001d"
            + "04030201"
        )
        self.assertEqual(h.serialize().hex(), expected_serialized_hex)

        expected_hash = hashlib.sha256(hashlib.sha256(h.serialize()).digest()).digest()
        self.assertEqual(h.hash(), expected_hash)

        target_equal = int.from_bytes(expected_hash, "big")
        self.assertTrue(h.is_valid_pow(target_equal))
        self.assertFalse(h.is_valid_pow(target_equal - 1))

        # Display hash is reversed for UX only; PoW comparison must remain internal-byte based.
        displayed_int = int.from_bytes(bytes.fromhex(h.hash_hex()), "big")
        self.assertNotEqual(displayed_int, target_equal)

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
        self.assertEqual(target, 0)
        self.assertFalse(pow_check.validate_compact(huge_bits))

    def test_validate_compact_rejects_zero_target(self) -> None:
        pow_check = ProofOfWork(ConsensusParams.regtest())
        self.assertFalse(pow_check.validate_compact(0x00000000))
        self.assertEqual(pow_check.get_target(0x00000000), 0)

    def test_validate_compact_rejects_negative_sign_bit(self) -> None:
        pow_check = ProofOfWork(ConsensusParams.regtest())
        self.assertFalse(pow_check.validate_compact(0x1D80FFFF))

    def test_validate_compact_rejects_overflow(self) -> None:
        pow_check = ProofOfWork(ConsensusParams.regtest())
        # exponent > 34 with non-zero coefficient triggers overflow.
        self.assertFalse(pow_check.validate_compact(0x23010000))

    def test_validate_compact_rejects_malformed_compact_size(self) -> None:
        pow_check = ProofOfWork(ConsensusParams.regtest())
        # Exponent=1 with tiny coefficient decodes to zero target.
        self.assertFalse(pow_check.validate_compact(0x01003456))

    def test_validate_compact_rejects_target_above_pow_limit(self) -> None:
        params = ConsensusParams.regtest()
        pow_check = ProofOfWork(params)
        # Canonical compact for pow_limit+1 should fail as above limit.
        above_limit_bits = pow_check.get_bits(params.pow_limit) + 1
        self.assertFalse(pow_check.validate_compact(above_limit_bits))

    def test_validate_compact_non_canonical_detection(self) -> None:
        params = ConsensusParams.regtest()
        pow_check = ProofOfWork(params)
        canonical = pow_check.get_bits(0x1234560000)
        # Shift mantissa right and exponent up to encode same-ish magnitude non-canonically.
        non_canonical = ((canonical >> 24) + 1) << 24 | ((canonical & 0x007FFFFF) >> 8)
        # Decode may be valid as an integer target, but canonical check should reject.
        self.assertFalse(pow_check.validate_compact(non_canonical, require_canonical=True))

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
