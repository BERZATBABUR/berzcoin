"""Ban manager for peer banning."""

import json
import time
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class BanEntry:
    address: str
    banned_until: int
    reason: str
    banned_at: int
    permanent: bool = False

    def is_active(self) -> bool:
        if self.permanent:
            return True
        return time.time() < self.banned_until

    def to_dict(self) -> Dict[str, object]:
        return {
            "address": self.address,
            "banned_until": int(self.banned_until),
            "reason": self.reason,
            "banned_at": int(self.banned_at),
            "permanent": bool(self.permanent),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, object]) -> "BanEntry":
        return cls(
            address=str(raw.get("address", "")),
            banned_until=int(raw.get("banned_until", 0) or 0),
            reason=str(raw.get("reason", "")),
            banned_at=int(raw.get("banned_at", 0) or 0),
            permanent=bool(raw.get("permanent", False)),
        )

class BanManager:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else None
        self._ban_file = self.data_dir / "banlist.json" if self.data_dir else None
        self.bans: Dict[str, BanEntry] = {}
        self.offenses: Dict[str, List[int]] = {}
        self._load()

    @staticmethod
    def _normalize_address(address: str) -> str:
        text = str(address or "").strip()
        if not text:
            return ""
        if text.startswith("[") and "]" in text:
            return text[1:text.find("]")]
        if text.count(":") > 1:
            # Plain IPv6 host (no explicit port).
            return text
        if ":" in text:
            return text.rsplit(":", 1)[0]
        return text

    def _load(self) -> None:
        if self._ban_file is None or not self._ban_file.exists():
            return
        try:
            with open(self._ban_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            entries = raw.get("bans", [])
            if not isinstance(entries, list):
                return
            for item in entries:
                if not isinstance(item, dict):
                    continue
                entry = BanEntry.from_dict(item)
                if entry.address:
                    self.bans[entry.address] = entry
            self.cleanup_expired()
        except Exception as e:
            logger.warning("Failed to load banlist: %s", e)

    def _save(self) -> None:
        if self._ban_file is None:
            return
        try:
            self._ban_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": int(time.time()),
                "bans": [entry.to_dict() for entry in self.bans.values()],
            }
            with open(self._ban_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning("Failed to persist banlist: %s", e)

    def ban(
        self,
        address: str,
        duration: int = 86400,
        reason: str = "",
        permanent: bool = False,
    ) -> None:
        normalized = self._normalize_address(address)
        if not normalized:
            return
        now = int(time.time())
        banned_until = 0 if permanent else now + max(1, int(duration))
        self.bans[normalized] = BanEntry(
            address=normalized,
            banned_until=banned_until,
            reason=reason,
            banned_at=now,
            permanent=bool(permanent),
        )
        self._save()
        if permanent:
            logger.info("Permanently banned %s: %s", normalized, reason)
        else:
            logger.info("Banned %s for %ss: %s", normalized, int(duration), reason)

    def unban(self, address: str) -> None:
        normalized = self._normalize_address(address)
        if normalized in self.bans:
            del self.bans[normalized]
            self._save()
            logger.info(f"Unbanned {normalized}")

    def is_banned(self, address: str) -> bool:
        normalized = self._normalize_address(address)
        if normalized not in self.bans:
            return False
        if self.bans[normalized].is_active():
            return True
        del self.bans[normalized]
        self._save()
        return False

    def get_banned(self) -> List[Dict]:
        self.cleanup_expired()
        return [
            {
                'address': e.address,
                'banned_until': e.banned_until,
                'reason': e.reason,
                'banned_at': e.banned_at,
                'permanent': bool(e.permanent),
            }
            for e in self.bans.values() if e.is_active()
        ]

    def record_offense(self, address: str, offense: str) -> bool:
        normalized = self._normalize_address(address)
        if not normalized:
            return False
        current_time = time.time()
        if normalized in self.offenses:
            self.offenses[normalized] = [
                t for t in self.offenses[normalized] if current_time - t < 3600
            ]
        else:
            self.offenses[normalized] = []
        self.offenses[normalized].append(current_time)
        offense_count = len(self.offenses[normalized])
        if offense_count >= 20:
            self.ban(normalized, reason=f"20 offenses: {offense}", permanent=True)
            return True
        if offense_count >= 10:
            self.ban(normalized, 86400, f"10 offenses: {offense}")
            return True
        if offense_count >= 5:
            self.ban(normalized, 3600, f"5 offenses: {offense}")
            return True
        return False

    def clear(self) -> None:
        self.bans.clear()
        self.offenses.clear()
        self._save()
        logger.info("Cleared all bans")

    def get_ban_count(self) -> int:
        return len([b for b in self.bans.values() if b.is_active()])

    def cleanup_expired(self) -> int:
        expired = [addr for addr, entry in self.bans.items() if not entry.is_active()]
        for addr in expired:
            del self.bans[addr]
        if expired:
            self._save()
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired bans")
        return len(expired)
