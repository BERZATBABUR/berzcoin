"""Peer scoring and persistent ban/reputation management."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from shared.utils.logging import get_logger

from .banman import BanManager

logger = get_logger()


@dataclass
class PeerScore:
    """Peer reputation score."""

    address: str
    score: int = 0
    last_seen: float = 0
    last_connected: float = 0
    failures: int = 0
    successes: int = 0
    bans: int = 0
    banned_until: float = 0
    manager: Optional["PeerScoringManager"] = field(default=None, repr=False, compare=False)

    def add_score(self, points: int) -> None:
        self.score += int(points)
        self.last_seen = time.time()

    def record_success(self) -> None:
        self.successes += 1
        self.last_connected = time.time()
        # Gradually forgive failed attempts on successful contact.
        self.failures = max(0, self.failures - 1)
        self.add_score(10)

    def record_failure(self, reason: str) -> None:
        _ = reason
        self.failures += 1
        self.add_score(-5)

    def is_banned(self) -> bool:
        if self.manager is not None:
            return self.manager.is_banned(self.address)
        return time.time() < self.banned_until

    def should_connect(self) -> bool:
        if self.is_banned():
            return False

        # Exponential backoff based on failures.
        if self.failures > 0:
            backoff = 60 * (2 ** min(self.failures - 1, 5))
            if time.time() - self.last_connected < backoff:
                return False

        return True

    def should_evict(self) -> bool:
        return self.is_banned() or self.score <= -40 or self.failures >= 12


class PeerScoringManager:
    """Manage peer reputation scores and persistent bans."""

    def __init__(self, network_hardening: bool = False):
        self.network_hardening = bool(network_hardening)
        self.scores: Dict[str, PeerScore] = {}
        self.thresholds = {
            "good": 50,
            "bad": -20,
            "temp_ban": -50,
            "long_ban": -80,
            "perm_ban": -120,
        }
        self.reason_penalties = {
            "connect_failed": -5,
            "handshake_failed": -10,
            "oversized_payload": -25,
            "protocol_violation": -20,
            "evicted_for_inbound_slot": -8,
            "invalid_block": -35,
            "invalid_transaction": -15,
            "relay_spam": -10,
            "addr_spam": -12,
            "stale_peer": -6,
            "msg_rate_limit": -12,
        }
        self._data_dir: Optional[Path] = None
        self._scores_file: Optional[Path] = None
        self.ban_manager = BanManager()

    def configure_persistence(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._scores_file = self._data_dir / "peer_scores.json"
        self.ban_manager = BanManager(data_dir=self._data_dir)
        self._load_scores()
        self.ban_manager.cleanup_expired()

    def _load_scores(self) -> None:
        if self._scores_file is None or not self._scores_file.exists():
            return
        try:
            with open(self._scores_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = raw.get("scores", {})
            if not isinstance(items, dict):
                return
            for address, payload in items.items():
                if not isinstance(payload, dict):
                    continue
                score = PeerScore(
                    address=str(address),
                    score=int(payload.get("score", 0)),
                    last_seen=float(payload.get("last_seen", 0.0) or 0.0),
                    last_connected=float(payload.get("last_connected", 0.0) or 0.0),
                    failures=int(payload.get("failures", 0)),
                    successes=int(payload.get("successes", 0)),
                    bans=int(payload.get("bans", 0)),
                    manager=self,
                )
                self.scores[str(address)] = score
        except Exception as e:
            logger.warning("Failed to load peer scores: %s", e)

    def _save_scores(self) -> None:
        if self._scores_file is None:
            return
        try:
            self._scores_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": int(time.time()),
                "scores": {
                    address: {
                        "score": int(score.score),
                        "last_seen": float(score.last_seen),
                        "last_connected": float(score.last_connected),
                        "failures": int(score.failures),
                        "successes": int(score.successes),
                        "bans": int(score.bans),
                    }
                    for address, score in self.scores.items()
                },
            }
            with open(self._scores_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save peer scores: %s", e)

    def get_score(self, address: str) -> PeerScore:
        if address not in self.scores:
            self.scores[address] = PeerScore(address=address, manager=self)
        else:
            self.scores[address].manager = self
        return self.scores[address]

    def is_banned(self, address: str) -> bool:
        return bool(self.ban_manager.is_banned(address))

    def record_good(self, address: str) -> None:
        score = self.get_score(address)
        score.record_success()
        self._cleanup()
        self._save_scores()

    def _apply_ban_policy(self, address: str, score: PeerScore, reason: str) -> None:
        # Keep temporary + long + permanent policy in one place.
        if score.failures >= 20 or score.score <= self.thresholds["perm_ban"]:
            self.ban_manager.ban(address, reason=reason, permanent=True)
            score.bans += 1
            score.banned_until = 0.0
            logger.warning("Permanently banned %s (%s)", address, reason)
            return

        if score.failures >= 12 or score.score <= self.thresholds["long_ban"]:
            self.ban_manager.ban(address, duration=86400 * 7, reason=reason)
            score.bans += 1
            score.banned_until = time.time() + 86400 * 7
            logger.warning("Long-ban peer %s (%s)", address, reason)
            return

        if score.failures >= 8 or score.score <= self.thresholds["temp_ban"]:
            self.ban_manager.ban(address, duration=86400, reason=reason)
            score.bans += 1
            score.banned_until = time.time() + 86400
            logger.warning("Temp-ban peer %s (%s)", address, reason)
            return

        # Also maintain offense-window based escalation.
        if self.ban_manager.record_offense(address, reason):
            score.bans += 1

    def record_bad(self, address: str, reason: str) -> None:
        score = self.get_score(address)
        score.record_failure(reason)
        score.add_score(int(self.reason_penalties.get(reason, 0)))
        self._apply_ban_policy(address, score, reason)
        self._save_scores()

    def record_invalid_block(self, address: str) -> None:
        self.record_bad(address, "invalid_block")

    def record_invalid_tx(self, address: str) -> None:
        self.record_bad(address, "invalid_transaction")

    def should_evict(self, address: str) -> bool:
        return self.get_score(address).should_evict()

    def list_banned(self) -> list:
        return self.ban_manager.get_banned()

    def set_ban(
        self,
        address: str,
        action: str = "add",
        bantime: int = 86400,
        reason: str = "manual",
    ) -> Dict[str, object]:
        cmd = str(action or "add").strip().lower()
        if cmd in {"remove", "unban", "delete"}:
            self.ban_manager.unban(address)
            return {"status": "unbanned", "address": address}

        permanent = int(bantime) <= 0
        if permanent:
            self.ban_manager.ban(address, reason=reason, permanent=True)
        else:
            self.ban_manager.ban(address, duration=int(bantime), reason=reason)
        return {
            "status": "banned",
            "address": address,
            "bantime": int(bantime),
            "permanent": bool(permanent),
            "reason": reason,
        }

    def clear_banned(self) -> Dict[str, object]:
        self.ban_manager.clear()
        return {"status": "cleared"}

    def get_best_peers(self, limit: int = 10) -> list:
        active = [s for s in self.scores.values() if not s.is_banned()]
        active.sort(key=lambda x: x.score, reverse=True)
        return [s.address for s in active[:limit]]

    def _cleanup(self, max_age: int = 86400 * 7) -> None:
        now = time.time()
        to_remove = []
        for addr, score in self.scores.items():
            if now - score.last_seen > max_age:
                to_remove.append(addr)

        for addr in to_remove:
            del self.scores[addr]
        self.ban_manager.cleanup_expired()

    def get_stats(self) -> dict:
        self.ban_manager.cleanup_expired()
        return {
            "total_peers": len(self.scores),
            "banned": int(self.ban_manager.get_ban_count()),
            "good_peers": sum(
                1 for s in self.scores.values() if s.score >= self.thresholds["good"]
            ),
            "bad_peers": sum(
                1 for s in self.scores.values() if s.score <= self.thresholds["bad"]
            ),
        }
