"""Advanced wallet control RPC handlers."""

import time
from typing import Any, Dict, List, Optional, Union

from node.wallet.core.wallet import Wallet
from node.wallet.storage.backup import WalletBackup


class WalletControlHandlers:
    """RPC handlers for advanced wallet control."""

    def __init__(self, node: Any) -> None:
        self.node = node

    async def list_wallets(self) -> List[str]:
        """Return names of loaded wallets (single-wallet node: at most one)."""
        if self.node.wallet and self.node.wallet.is_loaded:
            return [str(self.node.config.get("wallet", "default"))]
        return []

    async def load_wallet(self, filename: str, password: Optional[str] = None) -> Dict[str, Any]:
        """Load a wallet file from ``datadir/wallets/<filename>``."""
        if self.node.wallet and self.node.wallet.is_loaded:
            return {"error": "Wallet already loaded"}

        if not password:
            return {"error": "Password required to load wallet"}

        wallets_dir = self.node.config.get_datadir() / "wallets"
        wallets_dir.mkdir(parents=True, exist_ok=True)
        wallet_path = wallets_dir / filename

        self.node.wallet = Wallet(str(wallet_path), self.node.config.get("network", "mainnet"))
        try:
            if not self.node.wallet.load(password):
                self.node.wallet = None
                return {"error": "Failed to load wallet"}
        except ValueError as e:
            self.node.wallet = None
            return {"error": str(e)}

        return {"name": filename, "warning": ""}

    async def create_wallet(
        self, wallet_name: str = "default", password: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new wallet file under ``datadir/wallets/``."""
        if self.node.wallet and self.node.wallet.is_loaded:
            return {"error": "Wallet already loaded"}

        wallets_dir = self.node.config.get_datadir() / "wallets"
        wallets_dir.mkdir(parents=True, exist_ok=True)
        wallet_path = wallets_dir / wallet_name

        if not password:
            return {"error": "Password required for wallet creation"}

        self.node.wallet = Wallet(str(wallet_path), self.node.config.get("network", "mainnet"))
        try:
            mnemonic = self.node.wallet.create(password)
        except ValueError as e:
            self.node.wallet = None
            return {"error": str(e)}

        return {
            "name": wallet_name,
            "mnemonic": mnemonic,
            "warning": "Keep your mnemonic safe!",
        }

    async def unload_wallet(self) -> Dict[str, Any]:
        """Unload the current wallet reference (in-memory)."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        self.node.wallet._save()
        self.node.wallet = None
        return {"status": "unloaded"}

    async def backup_wallet(self, destination: Optional[str] = None) -> Dict[str, Any]:
        """Create a backup of the wallet file."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        backup = WalletBackup(self.node.wallet.wallet_path, destination)
        backup_name = f"wallet_{int(time.time())}"
        backup_path = backup.create_backup(backup_name)
        if not backup_path:
            return {"error": "Failed to create backup"}

        return {
            "status": "backup_created",
            "path": backup_path,
            "name": backup_name,
        }

    async def restore_wallet(
        self, backup_name: str, password: Optional[str] = None
    ) -> Dict[str, Any]:
        """Restore wallet from a named backup in the backup directory."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        pwd = password or self.node.config.get("walletpassphrase")
        if not pwd:
            return {"error": "Password required to reload wallet after restore"}

        backup = WalletBackup(self.node.wallet.wallet_path)
        if backup.restore_backup(backup_name):
            try:
                if not self.node.wallet.load(str(pwd).strip()):
                    return {"error": "Restored file present but failed to reload wallet"}
            except ValueError as e:
                return {"error": str(e)}
            return {"status": "restored", "backup": backup_name}

        return {"error": "Failed to restore backup"}

    async def list_backups(self) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """List backup files for the current wallet path."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        backup = WalletBackup(self.node.wallet.wallet_path)
        return backup.list_backups()

    async def get_wallet_addresses(
        self,
        account: Optional[str] = None,
        include_used: bool = True,
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """List addresses known to the keystore."""
        _ = account
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        addresses: List[Dict[str, Any]] = []
        for addr, key_info in self.node.wallet.keystore.keys.items():
            if include_used or not key_info.used:
                addresses.append(
                    {
                        "address": addr,
                        "label": key_info.label,
                        "used": key_info.used,
                        "created_at": key_info.created_at,
                        "path": key_info.path,
                    }
                )
        return addresses

    async def get_wallet_utxos(
        self,
        address: Optional[str] = None,
        min_conf: int = 1,
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """List UTXOs tracked by the wallet."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        best_height = self.node.chainstate.get_best_height()
        utxos = self.node.wallet.utxo_tracker.get_utxos_for_account()

        result: List[Dict[str, Any]] = []
        for utxo in utxos:
            if address and utxo.address != address:
                continue

            confirmations = (
                best_height - utxo.height + 1 if utxo.height > 0 else 0
            )

            if confirmations >= min_conf and not utxo.spent:
                result.append(
                    {
                        "txid": utxo.txid,
                        "vout": utxo.vout,
                        "amount": utxo.amount,
                        "address": utxo.address,
                        "confirmations": confirmations,
                        "spent": utxo.spent,
                    }
                )

        return result

    async def get_wallet_transactions(
        self, count: int = 100, skip: int = 0
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """Return recent transaction records for the default account from chain DB."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        account = self.node.wallet.account_manager.get_default_account()
        if not account:
            return []

        txids = account.transactions
        if skip:
            txids = txids[skip:]
        txids = txids[-count:]

        transactions: List[Dict[str, Any]] = []
        for txid in txids:
            tx_info = self.node.chainstate.get_transaction(txid)
            if tx_info:
                transactions.append(dict(tx_info))

        return transactions

    async def set_wallet_label(self, address: str, label: str) -> Dict[str, Any]:
        """Set label on a key we own."""
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        key_info = self.node.wallet.keystore.get_key(address)
        if not key_info:
            return {"error": "Address not in wallet"}

        key_info.label = label
        self.node.wallet._save()

        return {"status": "updated", "address": address, "label": label}

    async def lock_wallet(self) -> Dict[str, Any]:
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        self.node.wallet.lock()
        return {"status": "locked"}

    async def unlock_wallet(self, password: str, timeout: int = 0) -> Dict[str, Any]:
        _ = timeout
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        if self.node.wallet.unlock(password):
            return {"status": "unlocked", "timeout": timeout}

        return {"error": "Invalid password"}

    async def get_wallet_accounts(self) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        accounts: List[Dict[str, Any]] = []
        for acc in self.node.wallet.account_manager.get_all_accounts():
            accounts.append(
                {
                    "name": acc.name,
                    "index": acc.index,
                    "balance": acc.balance,
                    "transactions": len(acc.transactions),
                    "created_at": acc.created_at,
                }
            )
        return accounts

    async def create_account(self, name: str) -> Dict[str, Any]:
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        if self.node.wallet.account_manager.create_account(name):
            return {"status": "created", "name": name}

        return {"error": "Account already exists"}

    async def get_wallet_summary(self) -> Dict[str, Any]:
        if not self.node.wallet:
            return {"error": "No wallet loaded"}

        info = self.node.wallet.get_info()
        stats = self.node.wallet.utxo_tracker.get_stats()
        ks = info["keystore_stats"]

        return {
            "loaded": info["loaded"],
            "locked": info["locked"],
            "network": info["network"],
            "balance": info["balance"],
            "total_balance": self.node.wallet.account_manager.get_total_balance(),
            "utxo_count": stats["utxos"]["unspent"],
            "total_utxo_value": stats["total_value"],
            "accounts": info["account_summary"]["total_accounts"],
            "addresses": ks["total_keys"],
            "used_addresses": ks["used_keys"],
        }
