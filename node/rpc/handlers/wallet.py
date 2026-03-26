"""Wallet RPC handlers."""

import os
from typing import Any, Dict, List, Optional

from shared.utils.logging import get_logger

logger = get_logger()


class WalletHandlers:
    """RPC handlers for wallet operations."""

    def __init__(self, node: Any):
        self.node = node

    async def get_wallet_info(self) -> Dict[str, Any]:
        if not getattr(self.node, 'wallet', None):
            return {'error': 'Wallet not loaded'}

        w = self.node.wallet
        if getattr(w, "locked", True):
            balance_sats = 0
        else:
            # Wallet core in this repo does not keep account balances up to date.
            # Compute it from the node's UTXO set for the wallet's known addresses.
            addresses = list(w.keystore.keys.keys())
            balance_sats = sum(self.node.chainstate.get_balance(a) for a in addresses)

        return {
            'walletname': 'default',
            'walletversion': 1,
            'balance': balance_sats / 100000000,
            'unconfirmed_balance': 0,
            'immature_balance': 0,
            'txcount': 0,
            'keypoolsize': w.keystore.get_stats().get('total_keys', 0),
            'keypoololdest': 0,
            'paytxfee': 0.00001,
            'private_keys_enabled': not w.locked,
            'scanning': False
        }

    async def get_balance(self, account: Optional[str] = None, min_conf: int = 1) -> float:
        _ = min_conf
        if not getattr(self.node, 'wallet', None):
            return 0.0
        w = self.node.wallet
        if getattr(w, "locked", True):
            return 0.0

        # Ignore `account` for now; keystore holds addresses without a strict
        # link to wallet account balance in this repo.
        addresses = list(w.keystore.keys.keys())
        balance_sats = sum(self.node.chainstate.get_balance(a) for a in addresses)
        return balance_sats / 100000000

    async def get_new_address(self, account: Optional[str] = None, label: str = "") -> str:
        if not self.node.wallet:
            raise ValueError('Wallet not loaded')

        address = self.node.wallet.get_new_address(account, label)

        if not address:
            raise ValueError('Failed to generate address')

        return address

    async def send_to_address(self, address: str, amount: float,
                              fee_rate: Optional[int] = None, comment: str = "",
                              comment_to: str = "") -> str:
        _ = comment, comment_to
        if not self.node.wallet:
            raise ValueError('Wallet not loaded')

        satoshis = int(amount * 100000000)
        fee: Optional[int] = None
        if fee_rate is not None:
            fee = int(fee_rate * 250)

        txid = await self.node.wallet.send_to_address(address, satoshis, fee)

        if not txid:
            raise ValueError('Failed to send transaction')

        return txid

    async def list_unspent(self, min_conf: int = 1, max_conf: int = 9999999,
                           addresses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not self.node.wallet:
            return []

        utxos = self.node.wallet.utxo_tracker.get_utxos_for_account()

        result = []
        for utxo in utxos:
            if addresses and utxo.address not in addresses:
                continue

            if utxo.confirmations < min_conf or utxo.confirmations > max_conf:
                continue

            if utxo.spent:
                continue

            result.append({
                'txid': utxo.txid,
                'vout': utxo.vout,
                'address': utxo.address,
                'account': 'default',
                'scriptPubKey': utxo.script_pubkey.hex(),
                'amount': utxo.amount / 100000000,
                'confirmations': utxo.confirmations,
                'spendable': True,
                'solvable': True,
                'safe': True
            })

        return result

    async def list_transactions(self, account: Optional[str] = None, count: int = 10,
                                skip: int = 0) -> List[Dict[str, Any]]:
        _ = account, count, skip
        if not self.node.wallet:
            return []

        return []

    async def create_wallet(self, wallet_name: str = "default",
                            password: Optional[str] = None) -> Dict[str, Any]:
        if self.node.wallet:
            return {'error': 'Wallet already loaded'}

        from node.wallet.core.wallet import Wallet

        path = os.path.expanduser(f"~/.berzcoin/wallets/{wallet_name}.dat")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        if not password:
            return {'error': 'Password required for wallet creation'}

        wallet = Wallet(path, getattr(self.node, 'network', 'mainnet'))

        try:
            mnemonic = wallet.create(password)
        except ValueError as e:
            return {'error': str(e)}

        self.node.wallet = wallet

        return {
            'name': wallet_name,
            'warning': '',
            'mnemonic': mnemonic,
        }

    async def load_wallet(self, wallet_name: str = "default",
                          password: Optional[str] = None) -> Dict[str, Any]:
        if self.node.wallet:
            return {'error': 'Wallet already loaded'}

        from node.wallet.core.wallet import Wallet

        if not password:
            raise ValueError('Password required to load wallet')

        path = os.path.expanduser(f"~/.berzcoin/wallets/{wallet_name}.dat")
        wallet = Wallet(path, getattr(self.node, 'network', 'mainnet'))

        try:
            ok = wallet.load(password)
        except ValueError as e:
            raise ValueError(str(e)) from e
        if not ok:
            raise ValueError('Failed to load wallet')

        self.node.wallet = wallet

        return {
            'name': wallet_name,
            'warning': ''
        }

    async def lock_wallet(self) -> str:
        if not self.node.wallet:
            raise ValueError('Wallet not loaded')

        self.node.wallet.lock()
        return "Wallet locked"

    async def unlock_wallet(self, password: str, timeout: int = 0) -> str:
        _ = timeout
        if not self.node.wallet:
            raise ValueError('Wallet not loaded')

        if not self.node.wallet.unlock(password):
            raise ValueError('Invalid password')

        return "Wallet unlocked"

    async def get_address_info(self, address: str) -> Dict[str, Any]:
        if not self.node.wallet:
            return {'error': 'Wallet not loaded'}

        key_info = self.node.wallet.keystore.get_key(address)

        return {
            'address': address,
            'ismine': key_info is not None,
            'iswatchonly': False,
            'isscript': False,
            'iswitness': address.startswith('bc1') or address.startswith('tb1') or address.startswith('bcrt1'),
            'label': key_info.label if key_info else '',
            'timestamp': key_info.created_at if key_info else 0
        }
