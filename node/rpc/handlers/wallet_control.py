"""Wallet control RPC handlers (simple private-key model only)."""

from typing import Any, Dict, List

from node.wallet.simple_wallet import SimpleWalletManager


class WalletControlHandlers:
    """RPC handlers for wallet control using the canonical simple wallet model."""

    def __init__(self, node: Any) -> None:
        self.node = node

    def _manager(self) -> SimpleWalletManager:
        manager = getattr(self.node, "simple_wallet_manager", None)
        if manager is None:
            manager = SimpleWalletManager(
                self.node.config.get_datadir(),
                network=self.node.config.get("network", "mainnet"),
            )
            setattr(self.node, "simple_wallet_manager", manager)
        return manager

    async def list_wallets(self) -> List[str]:
        """List known simple wallet addresses on disk."""
        return self._manager().list_wallets()

    async def load_wallet(self, private_key: str) -> Dict[str, Any]:
        """Activate wallet from private key (compat alias for loadwallet)."""
        private_key = str(private_key or "").strip()
        if not private_key:
            return {"error": "Private key required"}
        try:
            wallet = self._manager().activate_wallet(private_key)
        except Exception:
            return {"error": "Invalid private key"}
        return {"name": "simple", "address": wallet.address, "warning": ""}

    async def create_wallet(self, wallet_name: str = "default") -> Dict[str, Any]:
        """Create and activate a new simple private-key wallet."""
        _ = wallet_name
        wallet = self._manager().create_wallet()
        self._manager().active_wallet = wallet
        self._manager().active_private_key = wallet.private_key_hex
        return {
            "name": "simple",
            "private_key": wallet.private_key_hex,
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "mnemonic": wallet.mnemonic,
            "warning": "Store your private key safely.",
        }

    async def activate_wallet(self, private_key: str) -> Dict[str, Any]:
        """Explicit private-key activation entrypoint."""
        key = str(private_key or "").strip()
        if not key:
            return {"error": "Private key required"}
        try:
            wallet = self._manager().activate_wallet(key)
        except Exception:
            return {"error": "Invalid private key"}
        return {
            "status": "activated",
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
        }
