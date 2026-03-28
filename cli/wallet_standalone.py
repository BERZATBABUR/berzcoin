#!/usr/bin/env python3
"""BerzCoin wallet helper: local simple-wallet create + remote RPC wallet calls.

This is not full SPV: balance / send / newaddress talk to a full node's JSON-RPC
(the node's active private-key wallet). Local ``create`` writes a simple wallet
JSON file under ~/.berzcoin_wallet/ for backup/offline use.

RPC auth: user ``berzcoin`` + cookie (see --rpc-cookie-file) or --rpc-password.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional


DEFAULT_WALLET_DIR = Path.home() / ".berzcoin_wallet"
DEFAULT_RPC_USER = "berzcoin"


def _rpc_call(
    url: str,
    rpc_user: str,
    rpc_password: str,
    method: str,
    params: Optional[List[Any]] = None,
) -> Any:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": 1,
        }
    ).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{rpc_user}:{rpc_password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"RPC HTTP {e.code}: {e.read().decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"RPC connection failed: {e}") from e

    if not isinstance(body, dict):
        raise RuntimeError(f"Invalid RPC response: {body!r}")
    if body.get("error"):
        err = body["error"]
        if isinstance(err, dict):
            raise RuntimeError(err.get("message", str(err)))
        raise RuntimeError(str(err))
    return body.get("result")


def _load_cookie_secret(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if ":" in text:
        return text.split(":", 1)[1]
    return text


def resolve_rpc_password(args: argparse.Namespace) -> str:
    if args.rpc_password:
        return args.rpc_password
    cookie_path = Path(args.rpc_cookie_file).expanduser()
    if cookie_path.is_file():
        return _load_cookie_secret(cookie_path)
    # Bitcoin-style default next to datadir
    fallback = Path.home() / ".berzcoin" / ".cookie"
    if fallback.is_file():
        return _load_cookie_secret(fallback)
    raise SystemExit(
        "RPC auth required: pass --rpc-password or --rpc-cookie-file "
        "(or place a cookie at ~/.berzcoin/.cookie)."
    )


def rpc_url_from_node(node: str) -> str:
    if "://" in node:
        return node if node.endswith("/") else f"{node.rstrip('/')}/"
    if ":" in node:
        host, _, port = node.partition(":")
        return f"http://{host}:{port}/"
    return f"http://{node}:8332/"


class StandaloneWallet:
    """Local wallet file + JSON-RPC helpers for a remote full node."""

    def __init__(
        self,
        wallet_file: Path,
        network: str = "mainnet",
        node_rpc: str = "127.0.0.1:8332",
        rpc_user: str = DEFAULT_RPC_USER,
        rpc_password: str = "",
    ) -> None:
        self.wallet_file = wallet_file
        self.network = network
        self.rpc_url = rpc_url_from_node(node_rpc)
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password

    def create(self) -> dict:
        """Create a new simple private-key wallet on disk (local only)."""
        from node.wallet.simple_wallet import SimpleWallet

        self.wallet_file.parent.mkdir(parents=True, exist_ok=True)
        wallet = SimpleWallet.create(network=self.network)
        payload = wallet.to_dict()
        payload["created_at"] = int(time.time())
        self.wallet_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def get_balance(self) -> float:
        """Query balance from the remote node's wallet (RPC ``get_balance``)."""
        return float(
            _rpc_call(
                self.rpc_url,
                self.rpc_user,
                self.rpc_password,
                "get_balance",
                [],
            )
        )

    def send(self, to_address: str, amount_berz: float) -> str:
        """Send via remote node (RPC ``send_to_address``)."""
        return str(
            _rpc_call(
                self.rpc_url,
                self.rpc_user,
                self.rpc_password,
                "send_to_address",
                [to_address, amount_berz],
            )
        )

    def get_new_address(self) -> str:
        """New receiving address from the remote node's wallet."""
        return str(
            _rpc_call(
                self.rpc_url,
                self.rpc_user,
                self.rpc_password,
                "get_new_address",
                [],
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BerzCoin standalone helper: local wallet create + remote RPC wallet calls",
    )
    parser.add_argument(
        "command",
        choices=["create", "balance", "send", "address"],
        help="create = local wallet file; other commands use remote node RPC",
    )
    parser.add_argument(
        "--node",
        default="127.0.0.1:8332",
        help="Remote node host:port for RPC (default 127.0.0.1:8332)",
    )
    parser.add_argument(
        "--rpc-user",
        default=DEFAULT_RPC_USER,
        help="RPC HTTP Basic user (default berzcoin)",
    )
    parser.add_argument(
        "--rpc-password",
        default="",
        help="RPC HTTP Basic password (usually cookie secret; or use --rpc-cookie-file)",
    )
    parser.add_argument(
        "--rpc-cookie-file",
        default="",
        help="Path to berzcoin:secret cookie file",
    )
    parser.add_argument(
        "--wallet-file",
        default="",
        help=f"Local wallet path (default: {DEFAULT_WALLET_DIR}/<network>/wallet.dat)",
    )
    parser.add_argument(
        "--network",
        default="mainnet",
        choices=["mainnet", "testnet", "regtest"],
        help="Network for local wallet create",
    )
    parser.add_argument("--to", help="Recipient address (send)")
    parser.add_argument("--amount", type=float, help="Amount in BERZ (send)")

    args = parser.parse_args()

    wallet_path = (
        Path(args.wallet_file).expanduser()
        if args.wallet_file
        else DEFAULT_WALLET_DIR / args.network / "wallet.dat"
    )

    if args.command == "create":
        w = StandaloneWallet(wallet_path, network=args.network, node_rpc=args.node)
        created = w.create()
        print(f"[OK] Wallet file: {wallet_path}")
        print(f"Address: {created.get('address')}")
        print(f"Public key: {created.get('public_key')}")
        print(f"Private key: {created.get('private_key')}")
        print(f"Mnemonic: {created.get('mnemonic')}")
        return

    rpc_pw = resolve_rpc_password(args)
    sw = StandaloneWallet(
        wallet_path,
        network=args.network,
        node_rpc=args.node,
        rpc_user=args.rpc_user,
        rpc_password=rpc_pw,
    )

    if args.command == "balance":
        bal = sw.get_balance()
        print(f"Balance: {bal} BERZ (from remote node wallet)")
        return

    if args.command == "send":
        if args.to is None or args.amount is None:
            print("[!] send requires --to and --amount", file=sys.stderr)
            sys.exit(1)
        txid = sw.send(args.to, args.amount)
        print(f"[OK] Sent {args.amount} BERZ to {args.to}")
        print(f"TXID: {txid}")
        return

    if args.command == "address":
        addr = sw.get_new_address()
        print(f"New address: {addr}")
        return


if __name__ == "__main__":
    main()
