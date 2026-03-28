"""Mainnet genesis block and checkpoint helpers.

Values are BerzCoin-owned network anchors and must stay consistent with
``ConsensusParams.mainnet``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class MainnetGenesis:
    """Mainnet genesis configuration (data + helper methods)."""

    # BerzCoin mainnet genesis metadata.
    GENESIS: Dict[str, Any] = {
        "version": 1,
        "prev_block_hash": "00" * 32,
        "merkle_root": "2ed9e25352bd4cdbd52a7d5afc3b780f9dd5b2dd3425c66d8b8f1c45e72d74e2",
        "timestamp": 1774569600,
        "bits": 0x207FFFFF,
        "nonce": 24409,
        "hash": "0000a2b00a878937fe2431db054cc73784721f63ee8bacffe1e0aa0612f01f25",
    }

    # Checkpoints (block height -> hash). Add more as mainnet advances.
    CHECKPOINTS: Dict[int, str] = {
        0: "0000a2b00a878937fe2431db054cc73784721f63ee8bacffe1e0aa0612f01f25",
    }

    @classmethod
    def load_from_file(cls, path: Path) -> Dict[str, Any]:
        """Load genesis from a JSON file, fallback to defaults."""
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return dict(cls.GENESIS)

    @classmethod
    def get_checkpoint(cls, height: int) -> Optional[str]:
        """Return the closest checkpoint at height <= requested height."""
        best_height: Optional[int] = None
        for h in sorted(cls.CHECKPOINTS.keys()):
            if h <= height:
                best_height = h
            else:
                break
        return cls.CHECKPOINTS.get(best_height) if best_height is not None else None

    @classmethod
    def is_checkpoint_valid(cls, height: int, block_hash: str) -> bool:
        """Validate a block hash against the closest checkpoint (if any)."""
        chk = cls.get_checkpoint(height)
        if chk is None:
            return True
        return chk == block_hash
