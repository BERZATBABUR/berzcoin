"""Peer scoring and ban management."""

import time
from typing import Dict, Optional
from dataclasses import dataclass, field
from shared.utils.logging import get_logger

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
    
    def add_score(self, points: int):
        """Add points to score."""
        self.score += points
        self.last_seen = time.time()
    
    def record_success(self):
        """Record successful connection."""
        self.successes += 1
        self.last_connected = time.time()
        self.add_score(10)
    
    def record_failure(self, reason: str):
        """Record connection failure."""
        self.failures += 1
        self.add_score(-5)
        
        # Ban after multiple failures
        if self.failures >= 10:
            self.banned_until = time.time() + 3600  # Ban for 1 hour
            self.bans += 1
    
    def is_banned(self) -> bool:
        """Check if peer is currently banned."""
        return time.time() < self.banned_until
    
    def should_connect(self) -> bool:
        """Check if we should attempt connection."""
        if self.is_banned():
            return False
        
        # Exponential backoff based on failures
        if self.failures > 0:
            backoff = 60 * (2 ** min(self.failures - 1, 5))
            if time.time() - self.last_connected < backoff:
                return False
        
        return True

    def should_evict(self) -> bool:
        """Whether this peer is an eviction candidate."""
        return self.is_banned() or self.score <= -40 or self.failures >= 12


class PeerScoringManager:
    """Manage peer reputation scores."""
    
    def __init__(self):
        """Initialize scoring manager."""
        self.scores: Dict[str, PeerScore] = {}
        self.thresholds = {
            'good': 50,
            'bad': -20,
            'ban': -50
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
        }
    
    def get_score(self, address: str) -> PeerScore:
        """Get or create peer score."""
        if address not in self.scores:
            self.scores[address] = PeerScore(address)
        return self.scores[address]
    
    def record_good(self, address: str):
        """Record good behavior."""
        score = self.get_score(address)
        score.record_success()
        
        # Clean up old scores
        self._cleanup()
    
    def record_bad(self, address: str, reason: str):
        """Record bad behavior."""
        score = self.get_score(address)
        score.record_failure(reason)
        score.add_score(int(self.reason_penalties.get(reason, 0)))
        
        # Auto-ban if score too low
        if score.score <= self.thresholds['ban']:
            score.banned_until = time.time() + 86400  # Ban for 24 hours
            logger.warning(f"Auto-banned {address} for {reason}")
    
    def record_invalid_block(self, address: str):
        """Record invalid block from peer."""
        self.record_bad(address, "invalid_block")
    
    def record_invalid_tx(self, address: str):
        """Record invalid transaction from peer."""
        self.record_bad(address, "invalid_transaction")

    def should_evict(self, address: str) -> bool:
        return self.get_score(address).should_evict()
    
    def get_best_peers(self, limit: int = 10) -> list:
        """Get highest scoring peers."""
        active = [s for s in self.scores.values() if not s.is_banned()]
        active.sort(key=lambda x: x.score, reverse=True)
        return [s.address for s in active[:limit]]
    
    def _cleanup(self, max_age: int = 86400 * 7):
        """Clean up old scores."""
        now = time.time()
        to_remove = []
        for addr, score in self.scores.items():
            if now - score.last_seen > max_age:
                to_remove.append(addr)
        
        for addr in to_remove:
            del self.scores[addr]
    
    def get_stats(self) -> dict:
        """Get scoring statistics."""
        return {
            'total_peers': len(self.scores),
            'banned': sum(1 for s in self.scores.values() if s.is_banned()),
            'good_peers': sum(1 for s in self.scores.values() if s.score >= 50),
            'bad_peers': sum(1 for s in self.scores.values() if s.score <= -20)
        }
