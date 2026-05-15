"""Command-line interface for BerzCoin."""

import sys
import json
import argparse
import asyncio
import os
import re
import subprocess
import configparser
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

        p = subparsers.add_parser(
            "start-mainnet",
            help="Start mainnet node + web interface together (starter mode)",
        )
        p.add_argument(
            "--starter",
            action="store_true",
            help="Required safety flag for starter launch",
        )
        p.add_argument(
            "--join",
            metavar="IP:PORT",
            help="Join a starter peer while launching local node + interface",
        )
        p.add_argument(
            "--web-port",
            type=int,
            default=8080,
            help="Requested web interface port (default: 8080)",
        )

        BlockchainCommands.add_parser(subparsers)
        WalletCommands.add_parser(subparsers)
        MiningCommands.add_parser(subparsers)
        MempoolCommands.add_parser(subparsers)
        ControlCommands.add_parser(subparsers)

        return parser

    async def run(self, args: Optional[list] = None) -> int:
        parsed_args = self.parser.parse_args(args)

        if not self._validate_common_args(parsed_args):
            return 2

        if parsed_args.command == "start-mainnet":
            return self._run_start_mainnet(parsed_args)

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

    def _validate_common_args(self, args: argparse.Namespace) -> bool:
        rpcport = int(getattr(args, "rpcport", 0) or 0)
        if not 1 <= rpcport <= 65535:
            print(f"Error: invalid rpc port: {rpcport}", file=sys.stderr)
            return False

        command = str(getattr(args, "command", "") or "")
        if command == "sendrawtransaction":
            return self._validate_hex_arg(getattr(args, "hexstring", ""), "transaction hex")
        if command == "submitblock":
            return self._validate_hex_arg(getattr(args, "hexdata", ""), "block hex")
        if command in {"testmempoolaccept", "submitpackage"}:
            for item in list(getattr(args, "hexstrings", []) or []):
                if not self._validate_hex_arg(item, "transaction hex"):
                    return False
            return True
        if command == "generate":
            count = int(getattr(args, "numblocks", 0) or 0)
            if count <= 0:
                print("Error: numblocks must be > 0", file=sys.stderr)
                return False
            return True
        return True

    @staticmethod
    def _validate_hex_arg(value: str, label: str) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            print(f"Error: missing {label}", file=sys.stderr)
            return False
        if len(text) % 2 != 0 or not re.fullmatch(r"[0-9a-f]+", text):
            print(f"Error: malformed {label}", file=sys.stderr)
            return False
        return True

    @staticmethod
    def _read_mainnet_config(conf_path: str) -> dict[str, str]:
        parser = configparser.ConfigParser()
        parser.read(conf_path, encoding="utf-8")
        if not parser.has_section("main"):
            return {}
        return {k: str(v).strip() for k, v in parser.items("main")}

    @staticmethod
    def _looks_placeholder_passphrase(value: str) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return True
        markers = (
            "replace",
            "your_real",
            "strong_pass",
            "changeme",
            "example",
            "password",
        )
        return any(m in text for m in markers)

    def _run_start_mainnet(self, args: argparse.Namespace) -> int:
        starter_mode = bool(getattr(args, "starter", False))
        join_target = str(getattr(args, "join", "") or "").strip()
        if not starter_mode and not join_target:
            print("Error: start-mainnet requires either --starter or --join IP:PORT", file=sys.stderr)
            return 2
        if starter_mode and join_target:
            print("Error: use either --starter or --join, not both", file=sys.stderr)
            return 2

        datadir = os.path.expanduser(
            str(getattr(args, "datadir", "") or "~/.berzcoin_mainnet")
        )
        conf_path = os.path.expanduser(
            str(getattr(args, "conf", "") or os.path.join(datadir, "berzcoin.conf"))
        )
        if not os.path.isfile(conf_path):
            print(f"Error: config file not found: {conf_path}", file=sys.stderr)
            return 2

        settings = self._read_mainnet_config(conf_path)
        network = settings.get("network", "").strip().lower()
        if network and network != "mainnet":
            print(f"Error: config network must be mainnet (got: {network})", file=sys.stderr)
            return 2

        passphrase = settings.get("wallet_encryption_passphrase", "")
        env_passphrase = os.environ.get("BERZCOIN_WALLET_PASSPHRASE", "")
        chosen_passphrase = passphrase or env_passphrase
        if self._looks_placeholder_passphrase(chosen_passphrase):
            print(
                "Error: wallet_encryption_passphrase is missing or placeholder-like in mainnet config.",
                file=sys.stderr,
            )
            print(
                "Set a real passphrase in berzcoin.conf or BERZCOIN_WALLET_PASSPHRASE.",
                file=sys.stderr,
            )
            return 2

        p2p_bind = settings.get("bind", "0.0.0.0") or "0.0.0.0"
        p2p_port = int(settings.get("port", "8333") or "8333")
        rpc_bind = settings.get("rpcbind", "127.0.0.1") or "127.0.0.1"
        rpc_port = int(settings.get("rpcport", "8332") or "8332")
        web_port = int(getattr(args, "web_port", 8080) or 8080)

        print("Starting BerzCoin mainnet launcher")
        print(f"  config:  {conf_path}")
        print(f"  datadir: {datadir}")
        print(f"  p2p:     {p2p_bind}:{p2p_port}")
        print(f"  rpc:     {rpc_bind}:{rpc_port}")
        print(f"  web:     127.0.0.1:{web_port}")
        if join_target:
            print(f"  join:    {join_target}")
        else:
            print("  role:    starter")

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(repo_root, "scripts", "run_mainnet_interface.sh")
        if not os.path.isfile(launcher):
            print(f"Error: launcher not found: {launcher}", file=sys.stderr)
            return 2

        cmd = [
            launcher,
            "--datadir",
            datadir,
            "--web-port",
            str(web_port),
        ]
        if starter_mode:
            cmd.append("--starter")
        if join_target:
            cmd.extend(["--join", join_target])
        if os.name == "nt":
            cmd = ["bash", launcher, "--datadir", datadir, "--web-port", str(web_port)] + cmd[5:]
        env = os.environ.copy()
        env["BERZCOIN_V1_MINING_TARGET_SECS"] = "120"
        try:
            completed = subprocess.run(cmd, env=env, check=False)
        except FileNotFoundError as e:
            if os.name == "nt":
                print(
                    "Error: could not launch shell script on Windows. "
                    "Install Git Bash and ensure `bash` is on PATH.",
                    file=sys.stderr,
                )
                return 2
            raise e
        return int(completed.returncode)

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
        if args.command == 'getmempooldiagnostics':
            return await handler.mempool.get_mempool_diagnostics(getattr(args, "top_n", 20))
        if args.command == 'getrawmempool':
            return await handler.mempool.get_raw_mempool(getattr(args, "verbose", False))
        if args.command == 'sendrawtransaction':
            return await handler.mempool.send_raw_transaction(args.hexstring)
        if args.command == 'testmempoolaccept':
            return await handler.mempool.test_mempool_accept(args.hexstrings)
        if args.command == 'getmempoolentry':
            return await handler.mempool.get_mempool_entry(args.txid)
        if args.command == 'submitpackage':
            return await handler.mempool.submit_package(args.hexstrings)

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
        if args.command == 'walletpassphrase':
            return await handler.wallet.wallet_passphrase(args.passphrase, args.timeout)
        if args.command == 'walletlock':
            return await handler.wallet.wallet_lock()
        if args.command == 'importxpubwatchonly':
            return await handler.wallet.import_xpub_watchonly(args.xpub, args.label)
        if args.command == 'walletcreatefundedpsbt':
            return await handler.wallet.wallet_create_funded_psbt(args.address, args.amount, getattr(args, 'feerate', None))
        if args.command == 'walletprocesspsbt':
            return await handler.wallet.wallet_process_psbt(args.psbt, args.sign == 'true')
        if args.command == 'finalizepsbt':
            return await handler.wallet.finalize_psbt(args.psbt)
        if args.command == 'createmultisigpolicy':
            return await handler.wallet.create_multisig_policy(args.required, args.pubkeys, args.label)

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
        if args.command == 'addpeer':
            return await handler.control.add_peer(args.address, args.mode)
        if args.command == 'addnode':
            return await handler.control.add_peer(args.address, 'addnode')
        if args.command == 'quickjoin':
            return await handler.control.quick_join(args.address)
        if args.command == 'join-starter':
            return await handler.control.join_starter(args.address)
        if args.command == 'listpeers':
            return await handler.control.list_peers(getattr(args, 'verbose', False))
        if args.command == 'clearbanned':
            return await handler.control.clear_banned()
        if args.command == 'verifypeer':
            return await handler.control.verify_peer(
                args.target,
                getattr(args, 'verifier_id', ''),
                getattr(args, 'verifier_node', 'local'),
            )
        if args.command == 'verify-node':
            return await handler.control.verify_peer(
                args.target,
                getattr(args, 'verifier_id', ''),
                getattr(args, 'verifier_node', 'local'),
            )
        if args.command == 'joinnetwork':
            return await handler.control.join_network(
                args.seed_registry,
                args.self_ip,
                getattr(args, 'port', 8333),
                getattr(args, 'max_peers', 8),
            )
        if args.command == 'doctor-network':
            return await handler.control.doctor_network(args.peer)

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
