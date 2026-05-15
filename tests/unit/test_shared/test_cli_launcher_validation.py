"""Validation tests for cli.launcher argument safety."""

from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from cli.launcher import _resolve_start_settings


class TestCLILauncherValidation(unittest.TestCase):
    def _base_args(self) -> Namespace:
        return Namespace(
            config=None,
            network="regtest",
            port=18444,
            rpc_port=18443,
            data_dir="~/.berzcoin/regtest",
            connect=[],
            seed=[],
            auto_discover=False,
            seed_registry="~/.berzcoin/seed_registry.json",
            self_ip=None,
            max_discovery_peers=8,
            use_seeds=True,
            dry_run=True,
        )

    def test_invalid_port_rejected(self) -> None:
        args = self._base_args()
        args.port = 70000
        with self.assertRaises(RuntimeError):
            _resolve_start_settings(args)

    def test_same_rpc_and_p2p_port_rejected(self) -> None:
        args = self._base_args()
        args.rpc_port = args.port
        with self.assertRaises(RuntimeError):
            _resolve_start_settings(args)

    def test_invalid_self_ip_rejected(self) -> None:
        args = self._base_args()
        args.auto_discover = True
        args.self_ip = "not-an-ip"
        with self.assertRaises(RuntimeError):
            _resolve_start_settings(args)


if __name__ == "__main__":
    unittest.main()

