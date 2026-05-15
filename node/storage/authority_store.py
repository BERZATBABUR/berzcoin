"""Persistence for authority-chain attestations and verifier state."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Any

from shared.utils.logging import get_logger

logger = get_logger()


class AuthorityStore:
    """JSON-backed authority-chain state store."""

    def __init__(self, data_dir: Path, filename: str = "authority_chain.json") -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / filename

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as e:
            logger.warning("Failed to load authority store %s: %s", self.path, e)
        return {}

    def save(self, payload: Dict[str, Any]) -> bool:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            body = dict(payload or {})
            body["updated_at"] = int(time.time())
            self.path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            return True
        except Exception as e:
            logger.warning("Failed to persist authority store %s: %s", self.path, e)
            return False

