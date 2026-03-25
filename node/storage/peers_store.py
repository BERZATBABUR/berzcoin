"""Peer database storage management."""

import time
from typing import List, Optional, Dict, Any
from shared.utils.logging import get_logger
from .db import Database

logger = get_logger()

class PeersStore:
    """Peer storage manager."""

    def __init__(self, db: Database):
        self.db = db

    def add_peer(self, address: str, port: int, services: int = 0,
                 user_agent: str = "", height: int = 0) -> None:
        self.db.execute("""
            INSERT INTO peers
            (address, port, services, last_seen, user_agent, height, connected_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(address) DO UPDATE SET
                services = excluded.services,
                last_seen = excluded.last_seen,
                user_agent = excluded.user_agent,
                height = excluded.height,
                connected_count = connected_count + 1
        """, (address, port, services, int(time.time()), user_agent, height))

    def update_peer(self, address: str, **kwargs) -> None:
        if not kwargs:
            return
        fields = []
        values = []
        for key, value in kwargs.items():
            fields.append(f"{key} = ?")
            values.append(value)
        values.append(address)
        query = f"UPDATE peers SET {', '.join(fields)} WHERE address = ?"
        self.db.execute(query, tuple(values))

    def get_peer(self, address: str) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one("SELECT * FROM peers WHERE address = ?", (address,))

    def get_all_peers(self, limit: int = 1000) -> List[Dict[str, Any]]:
        return self.db.fetch_all("""
            SELECT * FROM peers
            WHERE banned_until < ?
            ORDER BY last_seen DESC
            LIMIT ?
        """, (int(time.time()), limit))

    def get_connected_peers(self, since: int = 3600) -> List[Dict[str, Any]]:
        cutoff = int(time.time()) - since
        return self.db.fetch_all("""
            SELECT * FROM peers
            WHERE last_connected > ? AND banned_until < ?
            ORDER BY last_connected DESC
        """, (cutoff, int(time.time())))

    def get_peers_for_connection(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.db.fetch_all("""
            SELECT * FROM peers
            WHERE banned_until < ?
            AND last_connected > ?
            ORDER BY (connected_count - failed_count) DESC, last_seen DESC
            LIMIT ?
        """, (int(time.time()), int(time.time()) - 86400, limit))

    def record_connection(self, address: str, success: bool) -> None:
        if success:
            self.db.execute("""
                UPDATE peers
                SET last_connected = ?, connected_count = connected_count + 1
                WHERE address = ?
            """, (int(time.time()), address))
        else:
            self.db.execute("""
                UPDATE peers
                SET failed_count = failed_count + 1
                WHERE address = ?
            """, (address,))

    def record_failure(self, address: str) -> None:
        self.db.execute("""
            UPDATE peers
            SET failed_count = failed_count + 1
            WHERE address = ?
        """, (address,))

    def get_peer_count(self) -> int:
        result = self.db.fetch_one("SELECT COUNT(*) as count FROM peers")
        return result['count'] if result else 0

    def cleanup(self, max_age: int = 86400 * 30) -> int:
        cutoff = int(time.time()) - max_age
        result = self.db.execute(
            "DELETE FROM peers WHERE last_seen < ? AND banned_until < ?",
            (cutoff, int(time.time()))
        )
        return result.rowcount

    def is_banned(self, address: str) -> bool:
        result = self.db.fetch_one("SELECT banned_until FROM peers WHERE address = ?", (address,))
        if result and result['banned_until'] > int(time.time()):
            return True
        return False
