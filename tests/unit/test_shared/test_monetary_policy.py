"""Unit tests for BerzCoin monetary policy constants."""

import unittest

from shared.consensus.params import (
    COIN,
    MAINNET_GENESIS_HASH,
    MAINNET_MAGIC,
    MAX_MONEY,
    MAX_SUPPLY_BERZ,
    REGTEST_GENESIS_HASH,
    REGTEST_MAGIC,
    TESTNET_GENESIS_HASH,
    TESTNET_MAGIC,
    ConsensusParams,
)
from shared.consensus.subsidy import get_max_supply


class TestMonetaryPolicy(unittest.TestCase):
    def test_base_unit_is_sat_style(self) -> None:
        self.assertEqual(COIN, 100_000_000)

    def test_supply_cap_constant_is_2_pow_24_berz(self) -> None:
        self.assertEqual(MAX_SUPPLY_BERZ, 2 ** 24)
        self.assertEqual(MAX_MONEY, (2 ** 24) * COIN)

    def test_mainnet_uses_two_minute_spacing_and_four_year_halving(self) -> None:
        params = ConsensusParams.mainnet()
        self.assertEqual(params.pow_target_spacing, 120)
        self.assertEqual(params.subsidy_halving_interval, 1_051_200)

    def test_issued_supply_stays_under_cap(self) -> None:
        params = ConsensusParams.mainnet()
        self.assertLessEqual(get_max_supply(params), params.max_money)

    def test_network_identity_uses_berzcoin_specific_genesis_hashes(self) -> None:
        bitcoin_mainnet_genesis = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
        self.assertNotEqual(MAINNET_GENESIS_HASH, bitcoin_mainnet_genesis)
        self.assertNotEqual(TESTNET_GENESIS_HASH, bitcoin_mainnet_genesis)
        self.assertNotEqual(REGTEST_GENESIS_HASH, bitcoin_mainnet_genesis)

    def test_network_magic_mapping_roundtrip(self) -> None:
        self.assertEqual(ConsensusParams.mainnet().message_magic, MAINNET_MAGIC)
        self.assertEqual(ConsensusParams.testnet().message_magic, TESTNET_MAGIC)
        self.assertEqual(ConsensusParams.regtest().message_magic, REGTEST_MAGIC)
        self.assertEqual(ConsensusParams.mainnet().get_network_name(), "mainnet")
        self.assertEqual(ConsensusParams.testnet().get_network_name(), "testnet")
        self.assertEqual(ConsensusParams.regtest().get_network_name(), "regtest")


if __name__ == "__main__":
    unittest.main()
