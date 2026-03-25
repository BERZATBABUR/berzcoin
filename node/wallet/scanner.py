"""Wallet UTXO scanner and rescan utilities.

This scanner is designed for *this* BerzCoin codebase:
- The node maintains an SQLite-backed UTXO set in `node/storage/utxo_store.py`
  where `ConnectBlock` populates an `address` column for standard script types.
- The wallet keystore stores a set of known addresses.

So the primary job of a "UTXO scanner" here is to (re)discover which wallet
addresses currently have UTXOs, and mark those addresses as used.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from node.wallet.core.keystore import KeyStore

logger = get_logger()


class WalletScanner:
    """Scan chain/UTXO set for wallet activity."""

    def __init__(self, keystore: KeyStore, utxo_store: UTXOStore, chainstate: Any):
        self.keystore = keystore
        self.utxo_store = utxo_store
        self.chainstate = chainstate

        self.scanning = False
        self.scan_progress = 0.0
        self.last_scanned_height = 0

    async def scan(
        self, start_height: int = 0, end_height: Optional[int] = None
    ) -> Dict[str, Any]:
        """Scan the chain for wallet outputs and mark addresses as used.

        Implementation notes:
        - For this repo, UTXOs are already indexed by address in SQLite, so we
          do *not* need to parse every script in every block to find wallet UTXOs.
        - We still accept (start_height, end_height) for future extension and UI
          progress reporting, but today the scan checks the UTXO set directly.
        """
        if self.scanning:
            return {"error": "Scan already in progress"}

        self.scanning = True
        self.scan_progress = 0.0

        try:
            addrs: Set[str] = set(self.keystore.keys.keys())
            if not addrs:
                return {"scanned": 0, "found_utxos": 0, "used_addresses": 0}

            tip = self.chainstate.get_best_height()
            if end_height is None:
                end_height = tip
            end_height = max(0, min(int(end_height), int(tip)))
            start_height = max(0, min(int(start_height), end_height))

            logger.info(
                "Wallet scan requested (%s..%s) for %s addresses",
                start_height,
                end_height,
                len(addrs),
            )

            found_utxos = 0
            used_addresses = 0

            # Query UTXO set per address. This is efficient for small address sets.
            # Increase limit if you expect many UTXOs per address.
            for i, addr in enumerate(sorted(addrs)):
                utxos = self.utxo_store.get_utxos_for_address(addr, limit=50_000)
                if utxos:
                    found_utxos += len(utxos)
                    used_addresses += 1
                    ki = self.keystore.keys.get(addr)
                    if ki:
                        ki.used = True

                # Progress is per-address here (not per-block).
                self.scan_progress = ((i + 1) / max(1, len(addrs))) * 100.0
                await asyncio.sleep(0)

            self.last_scanned_height = end_height

            return {
                "scanned": (end_height - start_height + 1) if end_height >= start_height else 0,
                "found_utxos": found_utxos,
                "used_addresses": used_addresses,
                "last_height": end_height,
            }
        finally:
            self.scanning = False

    async def rescan(self) -> Dict[str, Any]:
        """Full rescan from genesis (currently uses UTXO index)."""
        return await self.scan(0)

    async def rescan_since_height(self, height: int) -> Dict[str, Any]:
        """Rescan from a specific height (currently uses UTXO index)."""
        return await self.scan(int(height))

    def get_progress(self) -> Dict[str, Any]:
        return {
            "scanning": self.scanning,
            "progress": self.scan_progress,
            "last_scanned": self.last_scanned_height,
        }

