"""Unit tests for centralized validation limit helpers."""

import unittest

from node.validation.limits import ValidationLimits
from shared.consensus.params import ConsensusParams


class TestValidationLimits(unittest.TestCase):
    def test_from_params_reads_maturity_and_max_money(self) -> None:
        params = ConsensusParams.regtest()
        setattr(params, "coinbase_maturity", 123)

        limits = ValidationLimits.from_params(params)

        self.assertEqual(limits.coinbase_maturity, 123)
        self.assertEqual(limits.max_money, params.max_money)

    def test_coinbase_script_length_window(self) -> None:
        limits = ValidationLimits()
        self.assertFalse(limits.is_coinbase_script_length_valid(1))
        self.assertTrue(limits.is_coinbase_script_length_valid(2))
        self.assertTrue(limits.is_coinbase_script_length_valid(100))
        self.assertFalse(limits.is_coinbase_script_length_valid(101))

    def test_dust_helper_ignores_op_return_outputs(self) -> None:
        limits = ValidationLimits()
        self.assertTrue(limits.is_dust_output(100, b"\x51"))
        self.assertFalse(limits.is_dust_output(100, b"\x6ahello"))


if __name__ == "__main__":
    unittest.main()
