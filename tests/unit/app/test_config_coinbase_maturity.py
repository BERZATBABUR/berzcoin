"""Unit tests for config-driven coinbase maturity overrides."""

import tempfile
import unittest
from pathlib import Path

from node.app.config import Config


class TestConfigCoinbaseMaturity(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
