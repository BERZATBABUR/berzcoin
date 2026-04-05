"""Unit tests for config-driven coinbase maturity overrides."""

import tempfile
import unittest
from pathlib import Path

from node.app.config import Config
from shared.consensus.buried_deployments import HARDFORK_TX_V2, SOFTFORK_BIP34_STRICT


class TestConfigCoinbaseMaturity(unittest.TestCase):
    def test_network_hardening_defaults_to_false(self) -> None:
        cfg = Config()
        self.assertFalse(bool(cfg.get("network_hardening", True)))

    def test_network_hardening_parses_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "[main]\n"
                "network = regtest\n"
                "network_hardening = true\n",
                encoding="utf-8",
            )

            cfg = Config(str(conf))
            self.assertTrue(bool(cfg.get("network_hardening", False)))

    def test_regtest_uses_configured_coinbase_maturity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "[main]\n"
                "network = regtest\n"
                "coinbase_maturity = 1\n",
                encoding="utf-8",
            )

            cfg = Config(str(conf))
            params = cfg.get_network_params()

            self.assertEqual(int(getattr(params, "coinbase_maturity", 100)), 1)

    def test_negative_coinbase_maturity_is_clamped_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "[main]\n"
                "network = regtest\n"
                "coinbase_maturity = -5\n",
                encoding="utf-8",
            )

            cfg = Config(str(conf))
            params = cfg.get_network_params()

            self.assertEqual(int(getattr(params, "coinbase_maturity", 100)), 0)

    def test_config_activation_height_prefix_keys_map_to_consensus_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "[main]\n"
                "network = regtest\n"
                f"activation_height_{SOFTFORK_BIP34_STRICT} = 150\n"
                f"activation_height_{HARDFORK_TX_V2} = 300\n",
                encoding="utf-8",
            )

            cfg = Config(str(conf))
            params = cfg.get_network_params()
            custom = getattr(params, "custom_activation_heights", {})

            self.assertEqual(custom.get(SOFTFORK_BIP34_STRICT), 150)
            self.assertEqual(custom.get(HARDFORK_TX_V2), 300)

    def test_custom_activation_heights_inline_value_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "[main]\n"
                "network = regtest\n"
                f"custom_activation_heights = {SOFTFORK_BIP34_STRICT}:50,{HARDFORK_TX_V2}=75\n",
                encoding="utf-8",
            )

            cfg = Config(str(conf))
            params = cfg.get_network_params()
            custom = getattr(params, "custom_activation_heights", {})

            self.assertEqual(custom.get(SOFTFORK_BIP34_STRICT), 50)
            self.assertEqual(custom.get(HARDFORK_TX_V2), 75)

    def test_parse_activation_height_items_clamps_negative_values(self) -> None:
        parsed = Config.parse_activation_height_items(
            [f"{SOFTFORK_BIP34_STRICT}=-1", f"{HARDFORK_TX_V2}=20"]
        )
        self.assertEqual(parsed[SOFTFORK_BIP34_STRICT], 0)
        self.assertEqual(parsed[HARDFORK_TX_V2], 20)

    def test_legacy_activation_names_are_normalized_to_canonical(self) -> None:
        parsed = Config.parse_activation_height_items(
            ["softfork_strict_bip34=10", "hardfork_v2_tx_version=11"]
        )
        self.assertEqual(parsed[SOFTFORK_BIP34_STRICT], 10)
        self.assertEqual(parsed[HARDFORK_TX_V2], 11)


if __name__ == "__main__":
    unittest.main()
