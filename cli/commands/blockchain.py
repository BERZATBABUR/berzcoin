"""Blockchain CLI commands."""

import argparse
from typing import Any


class BlockchainCommands:
    """Blockchain CLI commands."""

    def __init__(self, handler: Any):
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser('getblockchaininfo', help='Get blockchain info')
        p.set_defaults(command='getblockchaininfo')

        p = subparsers.add_parser('getblock', help='Get block')
        p.add_argument('blockhash', help='Block hash')
        p.add_argument('--verbosity', '-v', type=int, default=1, help='Verbosity 0-2')
        p.set_defaults(command='getblock')

        p = subparsers.add_parser('getblockhash', help='Get block hash by height')
        p.add_argument('height', type=int, help='Block height')
        p.set_defaults(command='getblockhash')

        p = subparsers.add_parser('getblockcount', help='Get block count')
        p.set_defaults(command='getblockcount')

        p = subparsers.add_parser('getbestblockhash', help='Get best block hash')
        p.set_defaults(command='getbestblockhash')

        p = subparsers.add_parser('gettxout', help='Get transaction output')
        p.add_argument('txid', help='Transaction ID')
        p.add_argument('vout', type=int, help='Output index')
        p.add_argument('--includemempool', action='store_true', help='Include mempool')
        p.set_defaults(command='gettxout')

    async def get_blockchain_info(self):
        return await self.handler.call('get_blockchain_info')

    async def get_block(self, blockhash: str, verbosity: int = 1):
        return await self.handler.call('get_block', blockhash, verbosity)

    async def get_block_hash(self, height: int):
        return await self.handler.call('get_block_hash', height)

    async def get_block_count(self):
        return await self.handler.call('get_block_count')

    async def get_best_block_hash(self):
        return await self.handler.call('get_best_block_hash')

    async def get_tx_out(self, txid: str, vout: int, include_mempool: bool = False):
        return await self.handler.call('get_tx_out', txid, vout, include_mempool)
