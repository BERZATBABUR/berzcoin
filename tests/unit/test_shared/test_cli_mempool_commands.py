"""Unit tests for CLI mempool command wrappers."""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from cli.commands.mempool import MempoolCommands
from cli.main import BerzCoinCLI


class _HandlerStub:
    def __init__(self):
        self.calls = []

    async def call(self, method, *params):
        self.calls.append((method, list(params)))
        return {"ok": True, "method": method}


class TestCLIMempoolCommands(unittest.TestCase):
    def test_parser_exposes_getmempooldiagnostics(self) -> None:
        cli = BerzCoinCLI()
        args = cli.parser.parse_args(["getmempooldiagnostics", "--top-n", "7"])
        self.assertEqual(args.command, "getmempooldiagnostics")
        self.assertEqual(args.top_n, 7)

    def test_parser_exposes_submitpackage(self) -> None:
        cli = BerzCoinCLI()
        args = cli.parser.parse_args(["submitpackage", "aa", "bb", "cc"])
        self.assertEqual(args.command, "submitpackage")
        self.assertEqual(args.hexstrings, ["aa", "bb", "cc"])

    def test_mempool_command_maps_get_mempool_diagnostics_rpc(self) -> None:
        async def run() -> None:
            handler = _HandlerStub()
            mempool = MempoolCommands(handler)
            await mempool.get_mempool_diagnostics(9)
            self.assertEqual(handler.calls[-1], ("get_mempool_diagnostics", [9]))

        asyncio.run(run())

    def test_mempool_command_maps_submit_package_rpc(self) -> None:
        async def run() -> None:
            handler = _HandlerStub()
            mempool = MempoolCommands(handler)
            await mempool.submit_package(["00", "11"])
            self.assertEqual(handler.calls[-1], ("submit_package", [["00", "11"]]))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

