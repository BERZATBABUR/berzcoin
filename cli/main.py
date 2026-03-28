"""Command-line interface for BerzCoin."""

import sys
import json
import argparse
import asyncio
import os
from typing import Any, Optional

import aiohttp

from .commands import (
    BlockchainCommands,
    WalletCommands,
    MiningCommands,
    MempoolCommands,
    ControlCommands,
)


class BerzCoinCLI:
    """BerzCoin command-line interface."""

    def __init__(self) -> None:
        self.parser = self._create_parser()
        self.rpc_url = "http://127.0.0.1:8332"
        self.rpc_user = "berzcoin"
        self.rpc_password = ""

    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="BerzCoin RPC client")

        parser.add_argument('-conf', help='Configuration file')
        parser.add_argument('-datadir', help='Data directory')
        parser.add_argument('-rpcuser', help='RPC username')
        parser.add_argument('-rpcpassword', help='RPC password')
        parser.add_argument('-rpcport', type=int, default=8332, help='RPC port')
        parser.add_argument('-rpcconnect', default='127.0.0.1', help='RPC host')

        subparsers = parser.add_subparsers(dest='command', help='Command')

        BlockchainCommands.add_parser(subparsers)
        WalletCommands.add_parser(subparsers)
        MiningCommands.add_parser(subparsers)
        MempoolCommands.add_parser(subparsers)
        ControlCommands.add_parser(subparsers)

        return parser

    async def run(self, args: Optional[list] = None) -> int:
        parsed_args = self.parser.parse_args(args)

        if parsed_args.rpcuser:
            self.rpc_user = parsed_args.rpcuser
        if parsed_args.rpcpassword:
            self.rpc_password = parsed_args.rpcpassword

        self.rpc_url = f"http://{parsed_args.rpcconnect}:{parsed_args.rpcport}"

        if not self.rpc_password:
            datadir = getattr(parsed_args, "datadir", None)
            self.rpc_password = await self._get_cookie(datadir)
            if not self.rpc_password:
                cookie_hint = self._cookie_file_path(datadir)
                print(
                    "RPC auth: no cookie secret found. "
                    f"Expected file: {cookie_hint} (created when berzcoind starts). "
                    "Use the same -datadir as the node, or -rpcpassword with the value after 'berzcoin:' in .cookie.",
                    file=sys.stderr,
                )

        if not parsed_args.command:
            self.parser.print_help()
            return 1

        try:
            result = await self._execute_command(parsed_args)

            if result is not None:
                self._print_result(result)

            return 0

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    async def _execute_command(self, args: argparse.Namespace) -> Any:
        handler = CommandHandler(self.rpc_url, self.rpc_user, self.rpc_password)

        if args.command == 'getblockchaininfo':
            return await handler.blockchain.get_blockchain_info()
        if args.command == 'getblock':
            return await handler.blockchain.get_block(args.blockhash, args.verbosity)
        if args.command == 'getblockhash':
            return await handler.blockchain.get_block_hash(args.height)
        if args.command == 'getblockcount':
            return await handler.blockchain.get_block_count()
        if args.command == 'getbestblockhash':
            return await handler.blockchain.get_best_block_hash()
        if args.command == 'gettxout':
            return await handler.blockchain.get_tx_out(args.txid, args.vout, args.includemempool)

        if args.command == 'getmempoolinfo':
            return await handler.mempool.get_mempool_info()
        if args.command == 'getrawmempool':
            return await handler.mempool.get_raw_mempool(getattr(args, "verbose", False))
        if args.command == 'sendrawtransaction':
            return await handler.mempool.send_raw_transaction(args.hexstring)
        if args.command == 'testmempoolaccept':
            return await handler.mempool.test_mempool_accept(args.hexstrings)
        if args.command == 'getmempoolentry':
            return await handler.mempool.get_mempool_entry(args.txid)

        if args.command == 'getwalletinfo':
            return await handler.wallet.get_wallet_info()
        if args.command == 'getbalance':
            return await handler.wallet.get_balance(getattr(args, 'account', None), args.minconf)
        if args.command == 'getnewaddress':
            return await handler.wallet.get_new_address(getattr(args, 'account', None), args.label)
        if args.command == 'sendtoaddress':
            result = await handler.wallet.send_to_address(
                args.address,
                args.amount,
                getattr(args, 'feerate', None),
                getattr(args, 'comment', ''),
                '',
            )
            print(f"Transaction sent: {result}")
            return None
        if args.command == 'listunspent':
            return await handler.wallet.list_unspent(args.minconf, args.maxconf, args.addresses)

        if args.command == 'listwallets':
            return await handler.wallet.list_wallets()
        if args.command == 'loadwallet':
            return await handler.wallet.load_wallet(args.private_key)
        if args.command == 'createwallet':
            return await handler.wallet.create_wallet(args.name)
        if args.command == 'activatewallet':
            return await handler.wallet.activate_wallet(args.private_key)

        if args.command == 'getmininginfo':
            return await handler.mining.get_mining_info()
        if args.command == 'getblocktemplate':
            return await handler.mining.get_block_template()
        if args.command == 'submitblock':
            return await handler.mining.submit_block(args.hexdata)
        if args.command == 'getdifficulty':
            return await handler.mining.get_difficulty()
        if args.command == 'generate':
            return await handler.mining.generate(
                args.numblocks, getattr(args, 'address', None), getattr(args, 'maxtries', 1000000)
            )
        if args.command == 'setgenerate':
            return await handler.mining.set_generate(
                args.generate == 'true',
                getattr(args, 'threads', 1),
            )
        if args.command == 'getminingstatus':
            return await handler.mining.get_mining_status()
        if args.command == 'setminingaddress':
            return await handler.mining.set_mining_address(args.address)

        if args.command == 'getinfo':
            return await handler.control.get_info()
        if args.command == 'stop':
            return await handler.control.stop()
        if args.command == 'nodehelp':
            return await handler.control.help(getattr(args, 'rpccommand', None))
        if args.command == 'ping':
            return await handler.control.ping()
        if args.command == 'uptime':
            return await handler.control.uptime()
        if args.command == 'getnetworkinfo':
            return await handler.control.get_network_info()

        return None

    def _print_result(self, result: Any) -> None:
        if isinstance(result, (dict, list)):
            print(json.dumps(result, indent=2, default=str))
        else:
            print(result)

    @staticmethod
    def _cookie_file_path(datadir: Optional[str]) -> str:
        base = os.path.expanduser(datadir) if datadir else os.path.expanduser("~/.berzcoin")
        return os.path.join(base, ".cookie")

    async def _get_cookie(self, datadir: Optional[str] = None) -> str:
        cookie_path = self._cookie_file_path(datadir)

        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if ":" in content:
                    return content.split(":", 1)[1]
        except OSError:
            pass

        return ""


class CommandHandler:
    """Marshals JSON-RPC calls to the node."""

    def __init__(self, rpc_url: str, user: str, password: str):
        self.rpc_url = rpc_url
        self.auth = aiohttp.BasicAuth(user, password)
        self.blockchain = BlockchainCommands(self)
        self.wallet = WalletCommands(self)
        self.mining = MiningCommands(self)
        self.mempool = MempoolCommands(self)
        self.control = ControlCommands(self)

    async def call(self, method: str, *params: Any) -> Any:
        async with aiohttp.ClientSession() as session:
            payload = {
                'jsonrpc': '2.0',
                'method': method,
                'params': list(params),
                'id': 1
            }

            async with session.post(self.rpc_url, json=payload, auth=self.auth) as response:
                text = await response.text()
                if response.status != 200:
                    raise RuntimeError(f"RPC HTTP {response.status}: {text}")

                data = json.loads(text)

                if isinstance(data, dict) and data.get('error'):
                    err = data['error']
                    if isinstance(err, dict):
                        raise RuntimeError(err.get('message', str(err)))
                    raise RuntimeError(str(err))

                return data.get('result') if isinstance(data, dict) else data


def main() -> None:
    cli = BerzCoinCLI()
    exit_code = asyncio.run(cli.run())
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
