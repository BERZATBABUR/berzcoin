"""Wallet control RPC handlers (simple private-key model only)."""

from typing import Any, Dict, List

from node.wallet.simple_wallet import SimpleWalletManager, redact_secret
from shared.utils.logging import get_logger
from node.rpc.errors import RPCError


logger = get_logger()


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
                wallet_passphrase=self.node.config.get("wallet_encryption_passphrase", ""),
                allow_insecure_fallback=bool(self.node.config.get("wallet_allow_insecure_fallback", False)),
                default_unlock_timeout_secs=int(
                    self.node.config.get("wallet_default_unlock_timeout", 300)
                ),
            )
            setattr(self.node, "simple_wallet_manager", manager)
        return manager

    def _allow_wallet_debug_secrets(self) -> bool:
        cfg = self.node.config
        if not bool(cfg.get("wallet_debug_secrets", False)):
            return False
        network = str(cfg.get("network", "mainnet") or "mainnet").strip().lower()
        return network == "regtest" or bool(cfg.get("debug", False))

    async def list_wallets(self) -> List[str]:
        """List known simple wallet addresses on disk."""
        return self._manager().list_wallets()

    async def load_wallet(self, private_key: str) -> Dict[str, Any]:
        """Activate wallet from private key (compat alias for loadwallet)."""
        private_key = str(private_key or "").strip()
        if not private_key:
            raise ValueError("Private key required")
        try:
            wallet = self._manager().activate_wallet(private_key)
        except Exception:
            logger.warning("Wallet activation failed for key=%s", redact_secret(private_key))
            raise RPCError(-32000, "Invalid private key")
        return {"name": "simple", "address": wallet.address, "warning": ""}

    async def create_wallet(self, wallet_name: str = "default") -> Dict[str, Any]:
        """Create and activate a new simple private-key wallet."""
        _ = wallet_name
        wallet = self._manager().create_wallet()
        self._manager().active_wallet = wallet
        self._manager().active_private_key = wallet.private_key_hex
        result = {
            "name": "simple",
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "warning": "Wallet created and activated.",
        }
        if self._allow_wallet_debug_secrets():
            result["private_key"] = wallet.private_key_hex
            result["mnemonic"] = wallet.mnemonic
        return result

    async def activate_wallet(self, private_key: str) -> Dict[str, Any]:
        """Explicit private-key activation entrypoint."""
        key = str(private_key or "").strip()
        if not key:
            raise ValueError("Private key required")
        try:
            wallet = self._manager().activate_wallet(key)
        except Exception:
            logger.warning("Wallet activation failed for key=%s", redact_secret(key))
            raise RPCError(-32000, "Invalid private key")
        return {
            "status": "activated",
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
        }

    async def wallet_passphrase(self, passphrase: str, timeout: int) -> Dict[str, Any]:
        """Unlock active wallet for signing for a limited time."""
        manager = self._manager()
        if manager.get_active_wallet() is None:
            raise RPCError(-32000, "No active wallet")
        if not manager.wallet_passphrase(passphrase, int(timeout)):
            raise RPCError(-32000, "Invalid passphrase or wallet unavailable")
        return {
            "status": "unlocked",
            "timeout": int(timeout),
            "unlocked_until": int(getattr(manager, "_unlocked_until", 0)),
        }

    async def wallet_lock(self) -> Dict[str, Any]:
        """Lock active wallet immediately."""
        manager = self._manager()
        manager.lock_wallet()
        return {"status": "locked"}
