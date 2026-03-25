"""Mainnet genesis block and checkpoint helpers.

Important:
- The values below are placeholders (Bitcoin mainnet genesis + sample checkpoint).
- Replace them with BerzCoin's real mainnet genesis and checkpoints before treating
  this as consensus-critical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class MainnetGenesis:
    """Mainnet genesis configuration (data + helper methods)."""

    # Genesis block (placeholder; replace with actual BerzCoin mainnet genesis)
    GENESIS: Dict[str, Any] = {
        "version": 1,
        "prev_block_hash": "00" * 32,
        "merkle_root": "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b",
        "timestamp": 1231006505,
        "bits": 0x1D00FFFF,
        "nonce": 2083236893,
        "hash": "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
    }

    # Checkpoints (block height -> hash). Add more as your mainnet progresses.
    CHECKPOINTS: Dict[int, str] = {
        0: "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
        2016: "00000000000a4d0a2d4ae0e6e0d8f5c8c2b1e9a7d3f5e8b9c2d4f6a8e9b1c3d5",
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

