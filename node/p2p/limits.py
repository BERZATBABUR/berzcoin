"""Connection limits and rate limiting."""

import time
from typing import Dict, List, Tuple
from collections import defaultdict
from dataclasses import dataclass


class OutboundClass:
    """Outbound connection classes used for anti-eclipse policy."""

    ANCHOR = "anchor"
    FULL_RELAY = "full-relay"
    BLOCK_RELAY_ONLY = "block-relay-only"
    FEELER = "feeler"


@dataclass(frozen=True)
class OutboundPolicy:
    """High-level outbound policy knobs for hardened mode."""

    min_anchor_netgroups: int = 2
    target_anchor_peers: int = 2
    target_block_relay_only_peers: int = 2
    feeler_interval_secs: int = 120
    rotation_interval_secs: int = 30

class RateLimiter:
    def __init__(self, max_messages: int = 100, time_window: int = 60):
        self.max_messages = max_messages
        self.time_window = time_window
        self.messages: Dict[str, List[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        self.messages[key] = [t for t in self.messages[key] if now - t < self.time_window]
        if len(self.messages[key]) >= self.max_messages:
            return False
        self.messages[key].append(now)
        return True

    def get_count(self, key: str) -> int:
        now = time.time()
        return len([t for t in self.messages.get(key, []) if now - t < self.time_window])

    def clear(self, key: str = None) -> None:
        if key:
            self.messages.pop(key, None)
        else:
            self.messages.clear()

class ConnectionLimits:
    def __init__(self, max_connections: int = 125, max_per_ip: int = 10):
        self.max_connections = max_connections
        self.max_per_ip = max_per_ip
        self.connections: Dict[str, int] = defaultdict(int)

    def can_connect(self, ip: str) -> Tuple[bool, str]:
        total = sum(self.connections.values())
        if total >= self.max_connections:
            return False, f"Max connections reached ({self.max_connections})"
        if self.connections[ip] >= self.max_per_ip:
            return False, f"Max connections per IP reached ({self.max_per_ip})"
        return True, "OK"

    def add_connection(self, ip: str) -> None:
        self.connections[ip] += 1

    def remove_connection(self, ip: str) -> None:
        self.connections[ip] -= 1
        if self.connections[ip] <= 0:
            del self.connections[ip]

    def get_stats(self) -> Dict[str, int]:
        return {
            'total': sum(self.connections.values()),
            'max': self.max_connections,
            'unique_ips': len(self.connections),
            'max_per_ip': self.max_per_ip,
        }

class MessageLimits:
    def __init__(self):
        self.limits = {
            'version': RateLimiter(max_messages=1, time_window=60),
            'verack': RateLimiter(max_messages=1, time_window=60),
            'ping': RateLimiter(max_messages=10, time_window=60),
            'pong': RateLimiter(max_messages=10, time_window=60),
            'getheaders': RateLimiter(max_messages=5, time_window=60),
            'headers': RateLimiter(max_messages=10, time_window=60),
            'getblocks': RateLimiter(max_messages=5, time_window=60),
            'inv': RateLimiter(max_messages=100, time_window=60),
            'getdata': RateLimiter(max_messages=100, time_window=60),
            'tx': RateLimiter(max_messages=50, time_window=60),
            'block': RateLimiter(max_messages=10, time_window=60),
            'addr': RateLimiter(max_messages=10, time_window=60),
            'sendcmpct': RateLimiter(max_messages=4, time_window=60),
            'cmpctblock': RateLimiter(max_messages=20, time_window=60),
        }

    def allow(self, peer_ip: str, command: str) -> bool:
        key = f"{peer_ip}:{command}"
        limiter = self.limits.get(command)
        if not limiter:
            return True
        return limiter.allow(key)

    def get_remaining(self, peer_ip: str, command: str) -> int:
        key = f"{peer_ip}:{command}"
        limiter = self.limits.get(command)
        if not limiter:
            return -1
        count = limiter.get_count(key)
        return max(0, limiter.max_messages - count)
