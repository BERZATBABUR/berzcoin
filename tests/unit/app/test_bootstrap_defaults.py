"""Tests for operator bootstrap defaults and discovery safety checks."""

import tempfile
import unittest
from pathlib import Path

from node.app.config import Config


class TestBootstrapDefaults(unittest.TestCase):
    def test_dns_default_seeds_per_network(self) -> None:
        cfg = Config()
        cfg.set("network", "mainnet")
        self.assertEqual(cfg.get_dns_seed_hosts(), [])

        cfg.set("network", "testnet")
        self.assertEqual(cfg.get_dns_seed_hosts(), [])

        cfg.set("network", "regtest")
        self.assertEqual(cfg.get_dns_seed_hosts(), [])

    def test_validate_fails_without_discovery_source_non_regtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("datadir", tmp)
            cfg.set("network", "mainnet")
            cfg.set("dnsseed", False)
            cfg.set("dnsseeds", [])
            cfg.set("connect", [])
            cfg.set("addnode", [])
            cfg.set("bootstrap_enabled", False)
            cfg.set("allow_missing_bootstrap", False)

            self.assertFalse(cfg.validate())

    def test_validate_allows_missing_when_override_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("datadir", tmp)
            cfg.set("network", "mainnet")
            cfg.set("dnsseed", False)
            cfg.set("dnsseeds", [])
            cfg.set("connect", [])
            cfg.set("addnode", [])
            cfg.set("bootstrap_enabled", False)
            cfg.set("allow_missing_bootstrap", True)

            self.assertTrue(cfg.validate())

    def test_discovery_priority_connect_overrides_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("datadir", tmp)
            cfg.set("connect", ["203.0.113.10:8333"])
            cfg.set("addnode", ["198.51.100.2:8333"])

            # Also place a bootstrap file; connect must still dominate.
            bootstrap_path = Path(tmp) / "bootstrap_nodes.json"
            bootstrap_path.write_text('{"bootstrap_nodes": ["192.0.2.5:8333"]}', encoding="utf-8")

            sources = cfg.get_peer_discovery_sources()
            self.assertEqual(sources["connect"], ["203.0.113.10:8333"])
            self.assertEqual(sources["addnode"], [])
            self.assertEqual(sources["bootstrap_file"], [])
            self.assertEqual(sources["dns_seeds"], [])

    def test_bootstrap_file_accepts_object_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("datadir", tmp)
            bootstrap_path = Path(tmp) / "bootstrap_nodes.json"
            bootstrap_path.write_text(
                """
                {
                  "bootstrap_nodes": [
                    {"address": "198.51.100.12", "port": 8333},
                    {"address": "203.0.113.55"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )

            self.assertEqual(
                cfg.get_bootstrap_nodes(),
                ["198.51.100.12:8333", "203.0.113.55"],
            )

    def test_load_accepts_sectionless_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "berzcoin.conf"
            conf.write_text(
                "\n".join(
                    [
                        "network=regtest",
                        "datadir=/tmp/berzcoin-legacy",
                        "port=18444",
                        "rpcport=18443",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = Config(str(conf))
            self.assertEqual(cfg.get("network"), "regtest")
            self.assertEqual(cfg.get("datadir"), "/tmp/berzcoin-legacy")
            self.assertEqual(cfg.get("port"), 18444)
            self.assertEqual(cfg.get("rpcport"), 18443)


if __name__ == "__main__":
    unittest.main()
