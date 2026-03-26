"""Simple private-key based wallet - no password, just private key."""

import json
import time
from typing import Dict, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field

from shared.crypto.keys import PrivateKey, PublicKey
from shared.crypto.address import public_key_to_address
# Note: mnemonic utilities not available in this workspace; keep mnemonic empty
from shared.utils.logging import get_logger

logger = get_logger()


@dataclass
class SimpleWallet:
    """Simple wallet that uses private key as identity."""

    private_key_hex: str
    public_key_hex: str
    address: str
    mnemonic: str
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls) -> "SimpleWallet":
        """Create a new wallet with random private key."""
        # Generate private key
        private_key = PrivateKey()
        private_key_hex = private_key.to_hex()

        # Generate mnemonic
        mnemonic = generate_mnemonic()

        # Derive public key and address
        public_key = private_key.public_key()
        public_key_hex = public_key.to_bytes().hex()
        address = public_key_to_address(public_key)

        return cls(
            private_key_hex=private_key_hex,
            public_key_hex=public_key_hex,
            address=address,
            mnemonic=mnemonic,
        )

    @classmethod
    def from_private_key(cls, private_key_hex: str) -> "SimpleWallet":
        """Load wallet from private key."""
        private_key = PrivateKey(int(private_key_hex, 16))
        public_key = private_key.public_key()
        address = public_key_to_address(public_key)

        return cls(
            private_key_hex=private_key_hex,
            public_key_hex=public_key.to_bytes().hex(),
            address=address,
            mnemonic="",
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "private_key": self.private_key_hex,
            "public_key": self.public_key_hex,
            "address": self.address,
            "mnemonic": self.mnemonic,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SimpleWallet":
        """Create from dictionary."""
        return cls(
            private_key_hex=data["private_key"],
            public_key_hex=data["public_key"],
            address=data["address"],
            mnemonic=data.get("mnemonic", ""),
            created_at=data.get("created_at", time.time()),
        )


class SimpleWalletManager:
    """Manage simple wallets by private key."""

    def __init__(self, data_dir: Path):
        """Initialize wallet manager."""
        self.data_dir = data_dir
        self.wallets_dir = data_dir / "wallets"
        self.wallets_dir.mkdir(parents=True, exist_ok=True)
        self.active_wallet: Optional[SimpleWallet] = None
        self.active_private_key: Optional[str] = None

    def create_wallet(self) -> SimpleWallet:
        """Create a new wallet."""
        wallet = SimpleWallet.create()
        self._save_wallet(wallet)
        return wallet

    def activate_wallet(self, private_key: str) -> Optional[SimpleWallet]:
        """Activate wallet using private key."""
        # Try to load existing
        wallet = self._load_wallet(private_key)
        if not wallet:
            # Create new from private key
            wallet = SimpleWallet.from_private_key(private_key)
            self._save_wallet(wallet)

        self.active_wallet = wallet
        self.active_private_key = private_key
        logger.info(f"Wallet activated: {wallet.address[:16]}...")
        return wallet

    def deactivate_wallet(self) -> None:
        """Deactivate current wallet."""
        self.active_wallet = None
        self.active_private_key = None
        logger.info("Wallet deactivated")

    def get_active_wallet(self) -> Optional[SimpleWallet]:
        """Get active wallet."""
        return self.active_wallet

    def get_active_address(self) -> Optional[str]:
        """Get active wallet address."""
        return self.active_wallet.address if self.active_wallet else None

    def get_active_public_key(self) -> Optional[str]:
        """Get active wallet public key."""
        return self.active_wallet.public_key_hex if self.active_wallet else None

    def get_active_private_key(self) -> Optional[str]:
        """Get active wallet private key (USE WITH CARE)."""
        return self.active_private_key

    def get_balance(self, chainstate) -> int:
        """Get balance for active wallet from chain."""
        if not self.active_wallet:
            return 0

        # Query UTXO set for address
        utxos = chainstate.get_utxos_for_address(self.active_wallet.address, 1000)
        return sum(u.get("value", 0) for u in utxos)

    def _save_wallet(self, wallet: SimpleWallet) -> None:
        """Save wallet to disk."""
        wallet_file = self.wallets_dir / f"{wallet.address}.json"
        with open(wallet_file, "w") as f:
            json.dump(wallet.to_dict(), f, indent=2)
        logger.debug(f"Wallet saved: {wallet_file}")

    def _load_wallet(self, private_key: str) -> Optional[SimpleWallet]:
        """Load wallet by private key."""
        # Create a temp wallet to get address
        temp = SimpleWallet.from_private_key(private_key)
        wallet_file = self.wallets_dir / f"{temp.address}.json"

        if wallet_file.exists():
            with open(wallet_file, "r") as f:
                data = json.load(f)
                return SimpleWallet.from_dict(data)

        return None

    def list_wallets(self) -> List[str]:
        """List all wallet addresses."""
        wallets: List[str] = []
        for f in self.wallets_dir.glob("*.json"):
            wallets.append(f.stem)
        return wallets
