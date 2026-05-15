"""Configuration and network-parameter hardening tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from node.app.config import Config
from node.chain.chainstate import ChainState
from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from shared.consensus.params import ConsensusParams
from shared.protocol.codec import MessageCodec


class TestConfigNetworkParameters(unittest.TestCase):
    def test_network_defaults_are_separated(self):
        c_main = Config()
        c_main.set("network", "mainnet")
        c_main.apply_network_defaults()
        self.assertEqual(int(c_main.get("port")), 8333)
        self.assertEqual(int(c_main.get("rpcport")), 8332)
        self.assertTrue(str(c_main.get("datadir")).endswith("/mainnet"))

        c_test = Config()
        c_test.set("network", "testnet")
        c_test.apply_network_defaults()
        self.assertEqual(int(c_test.get("port")), 18333)
        self.assertEqual(int(c_test.get("rpcport")), 18332)
        self.assertTrue(str(c_test.get("datadir")).endswith("/testnet"))

        c_reg = Config()
        c_reg.set("network", "regtest")
        c_reg.apply_network_defaults()
        self.assertEqual(int(c_reg.get("port")), 18444)
        self.assertEqual(int(c_reg.get("rpcport")), 18443)
        self.assertTrue(str(c_reg.get("datadir")).endswith("/regtest"))

    def test_wrong_magic_rejected(self):
        mainnet = MessageCodec("mainnet").encode("ping", b"\x00" * 8)
        with self.assertRaises(ValueError):
            MessageCodec("regtest").decode(mainnet)

    def test_mainnet_unsafe_rpc_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("network", "mainnet")
            cfg.set("datadir", tmp)
            cfg.set("rpcbind", "0.0.0.0")
            cfg.set("rpcallowip", ["*"])
            cfg.set("rpc_require_auth", False)
            self.assertFalse(cfg.validate())

    def test_mainnet_public_bind_rejected_without_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("network", "mainnet")
            cfg.set("datadir", tmp)
            cfg.set("bind", "0.0.0.0")
            cfg.set("mainnet_allow_unsafe_bind", False)
            self.assertFalse(cfg.validate())

    def test_mainnet_public_bind_allowed_with_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.set("network", "mainnet")
            cfg.set("datadir", tmp)
            cfg.set("bind", "0.0.0.0")
            cfg.set("mainnet_allow_unsafe_bind", True)
            cfg.set("allow_missing_bootstrap", True)
            self.assertTrue(cfg.validate())

    def test_shared_datadir_across_networks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            c1 = Config()
            c1.set("network", "regtest")
            c1.set("datadir", tmp)
            self.assertTrue(c1.validate())

            c2 = Config()
            c2.set("network", "mainnet")
            c2.set("datadir", tmp)
            self.assertFalse(c2.validate())

    def test_invalid_config_fails(self):
        cfg = Config()
        cfg.set("network", "invalid")
        self.assertFalse(cfg.validate())

    def test_config_priority_defaults_file_env_cli(self):
        old = os.environ.get("BERZCOIN_RPCPORT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                conf = Path(tmp) / "berzcoin.conf"
                conf.write_text("[main]\nnetwork=regtest\nrpcport=19000\n", encoding="utf-8")
                os.environ["BERZCOIN_RPCPORT"] = "19001"
                cfg = Config(str(conf))  # file + env
                self.assertEqual(int(cfg.get("rpcport")), 19001)
                cfg.set("rpcport", 19002)  # cli-equivalent final override
                self.assertEqual(int(cfg.get("rpcport")), 19002)
                self.assertEqual(
                    Config.config_priority_order(),
                    ["defaults", "config_file", "environment", "cli_arguments"],
                )
        finally:
            if old is None:
                os.environ.pop("BERZCOIN_RPCPORT", None)
            else:
                os.environ["BERZCOIN_RPCPORT"] = old

    def test_wrong_genesis_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp)
            db = Database(datadir, "mainnet")
            db.connect()
            try:
                m = Migrations(db)
                register_standard_migrations(m)
                m.migrate()
                params = ConsensusParams.mainnet()
                params.genesis_block_hash = "00" * 32  # force mismatch with metadata
                chain = ChainState(db, params, str(datadir))
                with self.assertRaises(RuntimeError):
                    chain._load_and_validate_genesis_metadata("mainnet")
            finally:
                db.disconnect()

    def test_p2p_limits_from_config_are_applied(self):
        class _Cfg:
            def __init__(self, datadir: Path):
                self._d = datadir
                self._v = {
                    "maxconnections": 33,
                    "maxoutbound": 5,
                    "p2p_max_message_size": 1111,
                    "p2p_handshake_timeout_secs": 7,
                    "p2p_connect_timeout_secs": 6,
                    "p2p_ban_score_threshold": 42,
                    "p2p_inv_max_items": 900,
                    "p2p_getdata_max_items": 700,
                    "network_hardening": False,
                    "authority_chain_enabled": False,
                    "network": "regtest",
                    "port": 18444,
                }

            def get(self, key, default=None):
                return self._v.get(key, default)

            def get_datadir(self):
                return self._d

            def get_admission_mode(self):
                return "open"

            def get_min_verifier_votes(self):
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            cm = ConnectionManager(AddrMan(Path(tmp)), node_config=_Cfg(Path(tmp)))
            self.assertEqual(cm.max_connections, 33)
            self.assertEqual(cm.max_outbound, 5)
            self.assertEqual(cm._max_message_size, 1111)
            self.assertEqual(cm._handshake_timeout_secs, 7)
            self.assertEqual(cm._connect_timeout_secs, 6)
            self.assertEqual(cm._read_timeout_secs, 30)
            self.assertEqual(cm._write_timeout_secs, 15)
            self.assertEqual(cm._idle_timeout_secs, 180)
            self.assertEqual(cm._ban_score_threshold, 42)
            self.assertEqual(cm._inv_max_items, 900)
            self.assertEqual(cm._getdata_max_items, 700)


if __name__ == "__main__":
    unittest.main()
