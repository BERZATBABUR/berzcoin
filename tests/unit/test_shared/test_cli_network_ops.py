"""Unit tests for unified starter/join/doctor CLI flows."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from cli.commands.control import ControlCommands
from cli.main import BerzCoinCLI


class _DummyConn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ControlHandlerStub:
    def __init__(self):
        self.calls = []
        self.rpc_url = "http://127.0.0.1:8332"

    async def call(self, method, *params):
        self.calls.append((method, list(params)))
        if method == "setban":
            return {"status": "unbanned"}
        if method == "add_peer":
            target = str(params[0])
            if "bad" in target:
                return {"ok": False, "error": "connect_failed", "connected_now": False}
            return {"ok": True, "connected_now": True}
        if method == "list_peers":
            return {
                "ok": True,
                "connected_peers": [{"address": "10.1.1.2:8333"}],
            }
        if method == "get_network_info":
            return {
                "network_active": True,
                "connections": 1,
                "connections_in": 0,
                "connections_out": 1,
                "admission_metrics": {"disconnect_reasons": {"connect_failed": 1}},
            }
        if method == "listbanned":
            return [{"address": "10.1.1.2:8333", "reason": "connect_failed"}]
        return {"ok": True}


class TestCLINetworkOps(unittest.TestCase):
    def test_parser_exposes_join_starter_and_doctor_network(self) -> None:
        cli = BerzCoinCLI()
        args = cli.parser.parse_args(["join-starter", "10.1.1.2:8333"])
        self.assertEqual(args.command, "join-starter")
        self.assertEqual(args.address, "10.1.1.2:8333")

        args2 = cli.parser.parse_args(["doctor-network", "--peer", "10.1.1.2:8333"])
        self.assertEqual(args2.command, "doctor-network")
        self.assertEqual(args2.peer, "10.1.1.2:8333")

    def test_join_starter_connected(self) -> None:
        async def run() -> None:
            handler = _ControlHandlerStub()
            cc = ControlCommands(handler)
            with patch("cli.commands.control.socket.create_connection", return_value=_DummyConn()):
                result = await cc.join_starter("10.1.1.2:8333")
            self.assertEqual(result.get("status"), "CONNECTED")
            methods = [m for m, _ in handler.calls]
            # ban-clear + reconnect behavior
            self.assertIn("setban", methods)
            self.assertIn("add_peer", methods)
            self.assertIn("list_peers", methods)

        asyncio.run(run())

    def test_join_starter_preflight_failure_has_clear_reason(self) -> None:
        async def run() -> None:
            handler = _ControlHandlerStub()
            cc = ControlCommands(handler)
            with patch("cli.commands.control.socket.create_connection", side_effect=OSError("timed out")):
                result = await cc.join_starter("10.1.1.2:8333")
            self.assertEqual(result.get("status"), "FAILED")
            self.assertIn("preflight tcp failed", str(result.get("reason", "")).lower())

        asyncio.run(run())

    def test_doctor_network_reports_expected_diagnostics(self) -> None:
        async def run() -> None:
            handler = _ControlHandlerStub()
            cc = ControlCommands(handler)
            with patch("cli.commands.control.socket.create_connection", return_value=_DummyConn()):
                result = await cc.doctor_network("10.1.1.2:8333")
            self.assertIn("checks", result)
            checks = result["checks"]
            self.assertIn("node_running", checks)
            self.assertIn("rpc_cookie_auth", checks)
            self.assertIn("p2p_listening", checks)
            self.assertIn("peer_reachable", checks)
            self.assertIn("ban_status", checks)
            self.assertIn("handshake_diagnostics", checks)

        asyncio.run(run())

    def test_start_mainnet_refuses_placeholder_passphrase(self) -> None:
        cli = BerzCoinCLI()
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, "berzcoin.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(
                    "[main]\n"
                    "network = mainnet\n"
                    "wallet_encryption_passphrase = STRONG_PASS_A\n"
                )
            err = io.StringIO()
            with redirect_stderr(err):
                rc = asyncio.run(
                    cli.run(
                        [
                            "-conf",
                            conf,
                            "-datadir",
                            tmp,
                            "start-mainnet",
                            "--starter",
                        ]
                    )
                )
            self.assertEqual(rc, 2)
            self.assertIn("passphrase", err.getvalue().lower())


if __name__ == "__main__":
    unittest.main()

