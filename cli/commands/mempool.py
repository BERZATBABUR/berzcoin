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

    async def get_mempool_info(self) -> Any:
        return await self.handler.call("get_mempool_info")

    async def get_raw_mempool(self, verbose: bool = False) -> Any:
        return await self.handler.call("get_raw_mempool", verbose)

    async def send_raw_transaction(self, hexstring: str) -> Any:
        return await self.handler.call("send_raw_transaction", hexstring)

    async def test_mempool_accept(self, hexstrings: list) -> Any:
        return await self.handler.call("test_mempool_accept", hexstrings)

    async def get_mempool_entry(self, txid: str) -> Any:
        return await self.handler.call("get_mempool_entry", txid)
