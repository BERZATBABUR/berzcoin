"""Wallet CLI commands."""

import argparse
from typing import Any, List, Optional


class WalletCommands:
    """Wallet CLI commands."""

    def __init__(self, handler: Any):
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser('getwalletinfo', help='Get wallet info')
        p.set_defaults(command='getwalletinfo')

        p = subparsers.add_parser('getbalance', help='Get wallet balance')
        p.add_argument('--account', help='Account name')
        p.add_argument('--minconf', type=int, default=1, help='Minimum confirmations')
        p.set_defaults(command='getbalance')

        p = subparsers.add_parser('getnewaddress', help='Get new address')
        p.add_argument('--account', help='Account name')
        p.add_argument('--label', default='', help='Address label')
        p.set_defaults(command='getnewaddress')

        p = subparsers.add_parser('sendtoaddress', help='Send to address')
        p.add_argument('address', help='Recipient address')
        p.add_argument('amount', type=float, help='Amount in BTC')
        p.add_argument('--feerate', type=int, help='Fee rate in sat/vbyte')
        p.add_argument('--comment', default='', help='Comment')
        p.set_defaults(command='sendtoaddress')

        p = subparsers.add_parser('listunspent', help='List unspent outputs')
        p.add_argument('--minconf', type=int, default=1, help='Minimum confirmations')
        p.add_argument('--maxconf', type=int, default=9999999, help='Maximum confirmations')
        p.add_argument('--addresses', nargs='*', default=None, help='Filter addresses')
        p.set_defaults(command='listunspent')

        p = subparsers.add_parser('listwallets', help='List loaded wallets')
        p.set_defaults(command='listwallets')

        p = subparsers.add_parser('loadwallet', help='Activate wallet by private key (compat alias)')
        p.add_argument('private_key', help='Wallet private key hex')
        p.set_defaults(command='loadwallet')

        p = subparsers.add_parser('createwallet', help='Create a new wallet')
        p.add_argument('name', nargs='?', default='default', help='Wallet name')
        p.set_defaults(command='createwallet')

        p = subparsers.add_parser('activatewallet', help='Activate wallet by private key')
        p.add_argument('private_key', help='Wallet private key hex')
        p.set_defaults(command='activatewallet')


    async def get_wallet_info(self):
        return await self.handler.call('get_wallet_info')

    async def get_balance(self, account: Optional[str] = None, minconf: int = 1):
        return await self.handler.call('get_balance', account, minconf)

    async def get_new_address(self, account: Optional[str] = None, label: str = ''):
        return await self.handler.call('get_new_address', account, label)

    async def send_to_address(self, address: str, amount: float, feerate: Optional[int] = None,
                              comment: str = '', comment_to: str = ''):
        return await self.handler.call('send_to_address', address, amount, feerate, comment, comment_to)

    async def list_unspent(self, minconf: int = 1, maxconf: int = 9999999,
                           addresses: Optional[List[str]] = None):
        return await self.handler.call('list_unspent', minconf, maxconf, addresses)

    async def list_wallets(self) -> Any:
        return await self.handler.call('listwallets')

    async def load_wallet(self, private_key: str) -> Any:
        return await self.handler.call('loadwallet', private_key)

    async def create_wallet(self, name: str = 'default') -> Any:
        return await self.handler.call('createwallet', name)

    async def activate_wallet(self, private_key: str) -> Any:
        return await self.handler.call('activatewallet', private_key)
