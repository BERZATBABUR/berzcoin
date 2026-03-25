"""Ban manager for peer banning."""

import time
from typing import Dict, Set, Optional, List
from dataclasses import dataclass
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class BanEntry:
    address: str
    banned_until: int
    reason: str
    banned_at: int

    def is_active(self) -> bool:
        return time.time() < self.banned_until

class BanManager:
    def __init__(self):
        self.bans: Dict[str, BanEntry] = {}
        self.offenses: Dict[str, List[int]] = {}

    def ban(self, address: str, duration: int = 86400, reason: str = "") -> None:
        banned_until = int(time.time()) + duration
        self.bans[address] = BanEntry(address=address, banned_until=banned_until, reason=reason, banned_at=int(time.time()))
        logger.info(f"Banned {address} for {duration}s: {reason}")

    def unban(self, address: str) -> None:
        if address in self.bans:
            del self.bans[address]
            logger.info(f"Unbanned {address}")

    def is_banned(self, address: str) -> bool:
        if address not in self.bans:
            return False
        if self.bans[address].is_active():
            return True
        del self.bans[address]
        return False

    def get_banned(self) -> List[Dict]:
        return [{'address': e.address, 'banned_until': e.banned_until, 'reason': e.reason, 'banned_at': e.banned_at} for e in self.bans.values() if e.is_active()]

    def record_offense(self, address: str, offense: str) -> bool:
        current_time = time.time()
        if address in self.offenses:
            self.offenses[address] = [t for t in self.offenses[address] if current_time - t < 3600]
        else:
            self.offenses[address] = []
        self.offenses[address].append(current_time)
        offense_count = len(self.offenses[address])
        if offense_count >= 10:
            self.ban(address, 86400, f"10 offenses: {offense}")
            return True
        elif offense_count >= 5:
            self.ban(address, 3600, f"5 offenses: {offense}")
            return True
        return False

    def clear(self) -> None:
        self.bans.clear()
        self.offenses.clear()
        logger.info("Cleared all bans")

    def get_ban_count(self) -> int:
        return len([b for b in self.bans.values() if b.is_active()])

    def cleanup_expired(self) -> int:
        expired = [addr for addr, entry in self.bans.items() if not entry.is_active()]
        for addr in expired:
            del self.bans[addr]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired bans")
        return len(expired)
