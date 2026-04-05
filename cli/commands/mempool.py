"""Mempool CLI commands."""

import argparse
from typing import Any


class MempoolCommands:
    """Mempool JSON-RPC helpers."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("getmempoolinfo", help="Get mempool statistics")
        p.set_defaults(command="getmempoolinfo")

        p = subparsers.add_parser(
            "getmempooldiagnostics",
            help="Get detailed mempool diagnostics (reject/eviction stats, thresholds)",
        )
        p.add_argument(
            "--top-n",
            type=int,
            default=20,
            help="Number of top reject/eviction reasons and eviction candidates to return",
        )
        p.set_defaults(command="getmempooldiagnostics")

        p = subparsers.add_parser("getrawmempool", help="List mempool txids (verbose: fee/size details)")
        p.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Verbose object per tx",
        )
        p.set_defaults(command="getrawmempool")

        p = subparsers.add_parser("sendrawtransaction", help="Submit a raw hex transaction")
        p.add_argument("hexstring", help="Serialized transaction hex")
        p.set_defaults(command="sendrawtransaction")

        p = subparsers.add_parser(
            "testmempoolaccept",
            help="Test whether raw transactions would be accepted (no relay)",
        )
        p.add_argument(
            "hexstrings",
            nargs="+",
            help="One or more raw transaction hex strings",
        )
        p.set_defaults(command="testmempoolaccept")

        p = subparsers.add_parser("getmempoolentry", help="Get mempool entry for txid")
        p.add_argument("txid", help="Transaction id")
        p.set_defaults(command="getmempoolentry")

        p = subparsers.add_parser(
            "submitpackage",
            help="Submit a package of related raw transaction hex strings",
        )
        p.add_argument(
            "hexstrings",
            nargs="+",
            help="One or more raw transaction hex strings in package order (order is normalized by node)",
        )
        p.set_defaults(command="submitpackage")

    async def get_mempool_info(self) -> Any:
        return await self.handler.call("get_mempool_info")

    async def get_mempool_diagnostics(self, top_n: int = 20) -> Any:
        return await self.handler.call("get_mempool_diagnostics", int(top_n))

    async def get_raw_mempool(self, verbose: bool = False) -> Any:
        return await self.handler.call("get_raw_mempool", verbose)

    async def send_raw_transaction(self, hexstring: str) -> Any:
        return await self.handler.call("send_raw_transaction", hexstring)

    async def test_mempool_accept(self, hexstrings: list) -> Any:
        return await self.handler.call("test_mempool_accept", hexstrings)

    async def get_mempool_entry(self, txid: str) -> Any:
        return await self.handler.call("get_mempool_entry", txid)

    async def submit_package(self, hexstrings: list) -> Any:
        return await self.handler.call("submit_package", list(hexstrings))
