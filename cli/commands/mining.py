"""Mining CLI commands."""

import argparse
from typing import Any, Optional


class MiningCommands:
    """Mining CLI commands."""

    def __init__(self, handler: Any):
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser('getmininginfo', help='Get mining info')
        p.set_defaults(command='getmininginfo')

        p = subparsers.add_parser('getblocktemplate', help='Get block template')
        p.set_defaults(command='getblocktemplate')

        p = subparsers.add_parser('submitblock', help='Submit mined block')
        p.add_argument('hexdata', help='Block hex data')
        p.set_defaults(command='submitblock')

        p = subparsers.add_parser('getdifficulty', help='Get current difficulty')
        p.set_defaults(command='getdifficulty')

        p = subparsers.add_parser('generate', help='Generate blocks (regtest only)')
        p.add_argument('numblocks', type=int, help='Number of blocks')
        p.add_argument('--address', help='Mining address')
        p.add_argument('--maxtries', type=int, default=1000000, help='Maximum nonce tries')
        p.set_defaults(command='generate')

        p = subparsers.add_parser('setgenerate', help='Start/stop background mining (regtest)')
        p.add_argument(
            'generate',
            choices=['true', 'false'],
            help='Enable (true) or disable (false) mining',
        )
        p.add_argument('--threads', type=int, default=1, help='Number of mining threads')
        p.set_defaults(command='setgenerate')

        p = subparsers.add_parser('getminingstatus', help='Get mining status')
        p.set_defaults(command='getminingstatus')

        p = subparsers.add_parser('setminingaddress', help='Set mining reward address')
        p.add_argument('address', help='Mining reward address')
        p.set_defaults(command='setminingaddress')


    async def get_mining_info(self):
        return await self.handler.call('get_mining_info')

    async def get_block_template(self):
        return await self.handler.call('get_block_template')

    async def submit_block(self, hexdata: str):
        return await self.handler.call('submit_block', hexdata)

    async def get_difficulty(self):
        return await self.handler.call('get_difficulty')

    async def generate(self, numblocks: int, address: Optional[str] = None, maxtries: int = 1000000):
        return await self.handler.call('generate', numblocks, address, maxtries)

    async def set_generate(self, generate: bool, threads: int = 1) -> Any:
        return await self.handler.call('setgenerate', generate, threads)

    async def get_mining_status(self) -> Any:
        return await self.handler.call('getminingstatus')

    async def set_mining_address(self, address: str) -> Any:
        return await self.handler.call('setminingaddress', address)
