"""Ban list storage management."""

import time
from typing import List, Optional, Dict, Any
from shared.utils.logging import get_logger
from .db import Database

logger = get_logger()

class BansStore:
    """Ban list storage manager."""

    def __init__(self, db: Database):
        self.db = db

    def ban_peer(self, address: str, duration: int = 86400, reason: str = "") -> None:
        banned_until = int(time.time()) + duration
        self.db.execute("""
            INSERT OR REPLACE INTO bans
            (address, banned_until, reason, banned_at)
            VALUES (?, ?, ?, ?)
        """, (address, banned_until, reason, int(time.time())))
        self.db.execute("""
            UPDATE peers
            SET banned_until = ?
            WHERE address = ?
        """, (banned_until, address))
        logger.info(f"Banned peer {address} for {duration}s: {reason}")

    def unban_peer(self, address: str) -> None:
        self.db.execute("DELETE FROM bans WHERE address = ?", (address,))
        self.db.execute("UPDATE peers SET banned_until = 0 WHERE address = ?", (address,))
        logger.info(f"Unbanned peer {address}")

    def is_banned(self, address: str) -> bool:
        result = self.db.fetch_one("SELECT banned_until FROM bans WHERE address = ?", (address,))
        if result and result['banned_until'] > int(time.time()):
            return True
        return False

    def get_banned_peers(self) -> List[Dict[str, Any]]:
        return self.db.fetch_all("""
            SELECT * FROM bans
            WHERE banned_until > ?
            ORDER BY banned_at DESC
        """, (int(time.time()),))

    def get_ban_expiry(self, address: str) -> Optional[int]:
        result = self.db.fetch_one("SELECT banned_until FROM bans WHERE address = ?", (address,))
        return result['banned_until'] if result else None

    def expire_bans(self) -> int:
        result = self.db.execute("DELETE FROM bans WHERE banned_until <= ?", (int(time.time()),))
        expired = result.rowcount
        if expired > 0:
            logger.debug(f"Expired {expired} bans")
        return expired

    def get_ban_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(*) as count FROM bans WHERE banned_until > ?", (int(time.time()),))
        return result['count'] if result else 0

    def record_offense(self, address: str, offense: str) -> None:
        logger.warning(f"Offense recorded for {address}: {offense}")
        offenses = self._get_offense_count(address)
        if offenses >= 3:
            self.ban_peer(address, 86400, f"Multiple offenses: {offense}")

    def _get_offense_count(self, address: str) -> int:
        return 0

    def clear_all_bans(self) -> int:
        count = self.get_ban_count()
        self.db.execute("DELETE FROM bans")
        self.db.execute("UPDATE peers SET banned_until = 0")
        logger.info(f"Cleared {count} bans")
        return count
