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

        p = subparsers.add_parser('loadwallet', help='Load a wallet')
        p.add_argument('filename', help='Wallet filename')
        p.add_argument('--password', default=None, help='Wallet password')
        p.set_defaults(command='loadwallet')

        p = subparsers.add_parser('createwallet', help='Create a new wallet')
        p.add_argument('name', nargs='?', default='default', help='Wallet name')
        p.add_argument('--password', default=None, help='Wallet password')
        p.set_defaults(command='createwallet')

        p = subparsers.add_parser('unloadwallet', help='Unload current wallet')
        p.set_defaults(command='unloadwallet')

        p = subparsers.add_parser('backupwallet', help='Backup wallet')
        p.add_argument('--destination', default=None, help='Backup destination directory')
        p.set_defaults(command='backupwallet')

        p = subparsers.add_parser('restorewallet', help='Restore wallet from backup')
        p.add_argument('backup', help='Backup name')
        p.set_defaults(command='restorewallet')

        p = subparsers.add_parser('listbackups', help='List available backups')
        p.set_defaults(command='listbackups')

        p = subparsers.add_parser('getwalletaddresses', help='Get wallet addresses')
        p.add_argument('--account', default=None, help='Account name')
        p.add_argument(
            '--include-used',
            action=argparse.BooleanOptionalAction,
            default=True,
            help='Include used addresses (default: true)',
        )
        p.set_defaults(command='getwalletaddresses')

        p = subparsers.add_parser('getwalletutxos', help='Get wallet UTXOs')
        p.add_argument('--address', default=None, help='Filter by address')
        p.add_argument('--minconf', type=int, default=1, help='Minimum confirmations')
        p.set_defaults(command='getwalletutxos')

        p = subparsers.add_parser('getwallettransactions', help='Get wallet transactions')
        p.add_argument('--count', type=int, default=100, help='Number of transactions')
        p.add_argument('--skip', type=int, default=0, help='Number to skip')
        p.set_defaults(command='getwallettransactions')

        p = subparsers.add_parser('setwalletlabel', help='Set address label')
        p.add_argument('address', help='Bitcoin address')
        p.add_argument('label', help='Label')
        p.set_defaults(command='setwalletlabel')

        p = subparsers.add_parser('lockwallet', help='Lock wallet')
        p.set_defaults(command='lockwallet')

        p = subparsers.add_parser('unlockwallet', help='Unlock wallet')
        p.add_argument('password', help='Wallet password')
        p.add_argument('--timeout', type=int, default=0, help='Timeout in seconds')
        p.set_defaults(command='unlockwallet')

        p = subparsers.add_parser('getwalletaccounts', help='Get wallet accounts')
        p.set_defaults(command='getwalletaccounts')

        p = subparsers.add_parser('createaccount', help='Create new account')
        p.add_argument('name', help='Account name')
        p.set_defaults(command='createaccount')

        p = subparsers.add_parser('getwalletsummary', help='Get wallet summary')
        p.set_defaults(command='getwalletsummary')

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

    async def load_wallet(self, filename: str, password: Optional[str] = None) -> Any:
        return await self.handler.call('loadwallet', filename, password)

    async def create_wallet(self, name: str = 'default', password: Optional[str] = None) -> Any:
        return await self.handler.call('createwallet', name, password)

    async def unload_wallet(self) -> Any:
        return await self.handler.call('unloadwallet')

    async def backup_wallet(self, destination: Optional[str] = None) -> Any:
        return await self.handler.call('backupwallet', destination)

    async def restore_wallet(self, backup: str) -> Any:
        return await self.handler.call('restorewallet', backup)

    async def list_backups(self) -> Any:
        return await self.handler.call('listbackups')

    async def get_wallet_addresses(
        self,
        account: Optional[str] = None,
        include_used: bool = True,
    ) -> Any:
        return await self.handler.call('getwalletaddresses', account, include_used)

    async def get_wallet_utxos(
        self,
        address: Optional[str] = None,
        min_conf: int = 1,
    ) -> Any:
        return await self.handler.call('getwalletutxos', address, min_conf)

    async def get_wallet_transactions(self, count: int = 100, skip: int = 0) -> Any:
        return await self.handler.call('getwallettransactions', count, skip)

    async def set_wallet_label(self, address: str, label: str) -> Any:
        return await self.handler.call('setwalletlabel', address, label)

    async def lock_wallet(self) -> Any:
        return await self.handler.call('lockwallet')

    async def unlock_wallet(self, password: str, timeout: int = 0) -> Any:
        return await self.handler.call('unlockwallet', password, timeout)

    async def get_wallet_accounts(self) -> Any:
        return await self.handler.call('getwalletaccounts')

    async def create_account(self, name: str) -> Any:
        return await self.handler.call('createaccount', name)

    async def get_wallet_summary(self) -> Any:
        return await self.handler.call('getwalletsummary')
