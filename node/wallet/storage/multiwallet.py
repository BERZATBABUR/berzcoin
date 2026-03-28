"""Persistent multi-wallet index for simple wallet storage."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from shared.utils.logging import get_logger

logger = get_logger()


@dataclass(frozen=True)
class WalletEntry:
    """Metadata for a wallet tracked by the local wallet index."""

    address: str
    network: str
    file_name: str
    created_at: float
    label: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "WalletEntry":
        return cls(
            address=str(data.get("address", "")),
            network=str(data.get("network", "mainnet")),
            file_name=str(data.get("file_name", "")),
            created_at=float(data.get("created_at", time.time())),
            label=str(data.get("label", "")),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "address": self.address,
            "network": self.network,
            "file_name": self.file_name,
            "created_at": self.created_at,
            "label": self.label,
        }


class MultiWalletStore:
    """Maintain wallet metadata and default-wallet selection."""

    INDEX_FILE_NAME = "wallet_index.json"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.wallets_dir = self.data_dir / "wallets"
        self.wallets_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.wallets_dir / self.INDEX_FILE_NAME
        self._index: Dict[str, object] = self._load_index()

    def _empty_index(self) -> Dict[str, object]:
        return {"version": 1, "default_wallet": "", "wallets": []}

    def _load_index(self) -> Dict[str, object]:
        if not self.index_path.exists():
            return self._empty_index()
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("Invalid wallet index root")
            if "wallets" not in loaded or not isinstance(loaded["wallets"], list):
                raise ValueError("Invalid wallet index wallets list")
            loaded.setdefault("version", 1)
            loaded.setdefault("default_wallet", "")
            return loaded
        except Exception as exc:
            logger.warning("Failed to load wallet index (%s): %s", self.index_path, exc)
            return self._empty_index()

    def _save_index(self) -> None:
        temp_path = self.index_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, sort_keys=True)
        temp_path.replace(self.index_path)

    def _entries(self) -> List[WalletEntry]:
        raw = self._index.get("wallets", [])
        if not isinstance(raw, list):
            return []
        entries: List[WalletEntry] = []
        for item in raw:
            if isinstance(item, dict):
                entry = WalletEntry.from_dict(item)
                if entry.address:
                    entries.append(entry)
        return entries

    def _set_entries(self, entries: List[WalletEntry]) -> None:
        self._index["wallets"] = [entry.to_dict() for entry in entries]
        self._save_index()

    def upsert_wallet(
        self,
        address: str,
        network: str = "mainnet",
        label: str = "",
        file_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> WalletEntry:
        """Insert or update wallet metadata."""
        addr = str(address or "").strip()
        if not addr:
            raise ValueError("Wallet address is required")
        resolved_file = file_name or f"{addr}.json"
        now = float(created_at if created_at is not None else time.time())
        new_entry = WalletEntry(
            address=addr,
            network=str(network or "mainnet"),
            file_name=resolved_file,
            created_at=now,
            label=str(label or ""),
        )

        entries = self._entries()
        replaced = False
        for i, existing in enumerate(entries):
            if existing.address == addr:
                # Preserve original creation time unless explicitly overridden.
                if created_at is None:
                    new_entry = WalletEntry(
                        address=new_entry.address,
                        network=new_entry.network,
                        file_name=new_entry.file_name,
                        created_at=existing.created_at,
                        label=new_entry.label,
                    )
                entries[i] = new_entry
                replaced = True
                break
        if not replaced:
            entries.append(new_entry)
        self._set_entries(entries)
        return new_entry

    def remove_wallet(self, address: str) -> bool:
        """Remove wallet metadata entry by address."""
        addr = str(address or "").strip()
        entries = self._entries()
        kept = [entry for entry in entries if entry.address != addr]
        if len(kept) == len(entries):
            return False
        self._set_entries(kept)
        if self._index.get("default_wallet") == addr:
            self._index["default_wallet"] = ""
            self._save_index()
        return True

    def list_wallets(self, network: Optional[str] = None) -> List[WalletEntry]:
        """List wallets, optionally filtered by network name."""
        entries = self._entries()
        if network is not None:
            network_norm = str(network).strip().lower()
            entries = [entry for entry in entries if entry.network.lower() == network_norm]
        entries.sort(key=lambda e: e.created_at)
        return entries

    def get_wallet(self, address: str) -> Optional[WalletEntry]:
        """Get wallet metadata by address."""
        addr = str(address or "").strip()
        for entry in self._entries():
            if entry.address == addr:
                return entry
        return None

    def set_default_wallet(self, address: str) -> bool:
        """Set default wallet by address; returns False when wallet is unknown."""
        if not self.get_wallet(address):
            return False
        self._index["default_wallet"] = str(address)
        self._save_index()
        return True

    def get_default_wallet(self, network: Optional[str] = None) -> Optional[WalletEntry]:
        """Get default wallet entry, optionally constrained to a network."""
        default_address = str(self._index.get("default_wallet", "") or "")
        if not default_address:
            return None
        entry = self.get_wallet(default_address)
        if not entry:
            return None
        if network is None:
            return entry
        if entry.network.lower() != str(network).strip().lower():
            return None
        return entry

    def refresh_from_disk(self, network: Optional[str] = None) -> int:
        """Reconcile index from wallet JSON files; returns count of added wallets."""
        existing = {entry.address for entry in self._entries()}
        added = 0
        for wallet_file in self.wallets_dir.glob("*.json"):
            if wallet_file.name == self.INDEX_FILE_NAME:
                continue
            address = wallet_file.stem
            if address in existing:
                continue
            self.upsert_wallet(address=address, network=network or "mainnet", file_name=wallet_file.name)
            existing.add(address)
            added += 1
        return added
