"""Address manager for peer discovery."""

import json
import random
import socket
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class AddressInfo:
    address: str
    services: int = 1
    last_seen: int = 0
    last_attempt: int = 0
    success_count: int = 0
    fail_count: int = 0
    tried: bool = False

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 0
        return self.success_count / total

    @property
    def should_retry(self) -> bool:
        if self.fail_count == 0:
            return True
        backoff = 60 * (2 ** min(self.fail_count - 1, 10))
        return time.time() - self.last_attempt >= backoff

class AddrMan:
    def __init__(self, max_addresses: int = 10000, data_dir: Optional[Path] = None):
        self.max_addresses = max_addresses
        self.addresses: Dict[str, AddressInfo] = {}
        self.new_addresses: Set[str] = set()
        self.tried_addresses: Set[str] = set()
        self.static_peers: Set[str] = set()
        self.static_peer_priority: Dict[str, int] = {}
        self.anchor_peers: Set[str] = set()
        self.data_dir = Path(data_dir) if data_dir else None
        self.anchor_file = (
            self.data_dir / "anchors.json" if self.data_dir else None
        )
        self.last_save = time.time()
        self.load_anchor_peers()

    def add(self, address: str, services: int = 1, source: str = None) -> bool:
        if address in self.addresses:
            self.addresses[address].last_seen = int(time.time())
            self.addresses[address].services |= services
            return True
        if len(self.addresses) >= self.max_addresses:
            self._evict_oldest()
        info = AddressInfo(address=address, services=services, last_seen=int(time.time()))
        self.addresses[address] = info
        self.new_addresses.add(address)
        logger.debug(f"Added address: {address}")
        return True

    def add_many(self, addresses: List[Tuple[str, int]]) -> None:
        for address, services in addresses:
            self.add(address, services)

    def add_from_dns_seed(self, seed_host: str, default_port: int = 8333) -> int:
        """Resolve a DNS seed hostname and add IPv4 ``host:port`` entries (peer discovery / PEX-style bootstrap)."""
        added = 0
        try:
            addrinfo = socket.getaddrinfo(
                seed_host,
                default_port,
                socket.AF_INET,
                socket.SOCK_STREAM,
            )
        except OSError as e:
            logger.error("DNS seed failed for %s: %s", seed_host, e)
            return 0

        seen: Set[str] = set()
        for _fam, _typ, _proto, _canon, sockaddr in addrinfo:
            if len(sockaddr) < 2:
                continue
            host = sockaddr[0]
            port = int(sockaddr[1])
            address = f"{host}:{port}"
            if address in seen:
                continue
            seen.add(address)
            was_new = address not in self.addresses
            self.add(address)
            if was_new:
                added += 1

        if seen:
            logger.info("DNS seed %s: added %s new address(es) (%s unique)", seed_host, added, len(seen))
        return added

    def add_static_peer(self, address: str, priority: int = 50) -> None:
        """Prefer this address for outbound tries (bootstrap / addnode / connect)."""
        address = address.strip()
        if not address:
            return
        self.static_peers.add(address)
        current = self.static_peer_priority.get(address, 10_000)
        self.static_peer_priority[address] = min(current, int(priority))
        self.add(address)
        if address in self.new_addresses:
            self.new_addresses.remove(address)
        self.tried_addresses.add(address)
        info = self.addresses.get(address)
        if info:
            info.tried = True
        logger.info("Added static peer: %s", address)

    def add_bootstrap_nodes(self, nodes: List[str], priority: int = 20) -> None:
        for node in nodes:
            self.add_static_peer(node, priority=priority)
        if nodes:
            logger.info("Loaded %s bootstrap address(es) into AddrMan", len(nodes))

    def get_static_peers(self) -> Set[str]:
        return set(self.static_peers)

    def set_anchor_peers(self, peers: List[str]) -> None:
        cleaned: Set[str] = set()
        for peer in peers:
            text = str(peer or "").strip()
            if text:
                cleaned.add(text)
                self.add(text)
        self.anchor_peers = cleaned
        self.save_anchor_peers()

    def add_anchor_peer(self, address: str) -> None:
        text = str(address or "").strip()
        if not text:
            return
        self.anchor_peers.add(text)
        self.add(text)
        self.save_anchor_peers()

    def get_anchor_peers(self) -> Set[str]:
        return set(self.anchor_peers)

    def load_anchor_peers(self) -> None:
        if self.anchor_file is None or not self.anchor_file.exists():
            return
        try:
            with open(self.anchor_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            peers = payload.get("anchors", [])
            if not isinstance(peers, list):
                return
            cleaned = [str(p).strip() for p in peers if str(p).strip()]
            self.anchor_peers = set(cleaned)
            for peer in cleaned:
                self.add(peer)
        except Exception as e:
            logger.debug("Failed to load anchors: %s", e)

    def save_anchor_peers(self) -> None:
        if self.anchor_file is None:
            return
        try:
            self.anchor_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "anchors": sorted(self.anchor_peers),
                "saved_at": int(time.time()),
            }
            with open(self.anchor_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.debug("Failed to save anchors: %s", e)

    def mark_good(self, address: str) -> None:
        if address not in self.addresses:
            return
        info = self.addresses[address]
        info.success_count += 1
        info.last_attempt = int(time.time())
        if address in self.new_addresses:
            self.new_addresses.remove(address)
            self.tried_addresses.add(address)
            info.tried = True
        logger.debug(f"Marked address as good: {address}")

    def mark_failed(self, address: str) -> None:
        if address not in self.addresses:
            return
        info = self.addresses[address]
        info.fail_count += 1
        info.last_attempt = int(time.time())
        logger.debug(f"Marked address as failed: {address}")

    def get_addresses(self, count: int = 10) -> List[str]:
        addresses = []
        # Always prioritize explicitly configured static peers (addnode/connect).
        # These are operator-selected and should be retried promptly even when a
        # prior attempt failed during startup ordering races.
        ordered_static = sorted(
            self.static_peers,
            key=lambda a: (self.static_peer_priority.get(a, 10_000), a),
        )
        for addr in ordered_static:
            if addr in self.addresses and addr not in addresses:
                addresses.append(addr)
                if len(addresses) >= count:
                    return addresses

        tried_list = [(addr, self.addresses[addr]) for addr in self.tried_addresses if self.addresses[addr].should_retry]
        tried_list.sort(key=lambda x: x[1].success_rate, reverse=True)
        for addr, _ in tried_list[:count]:
            if addr not in addresses:
                addresses.append(addr)
                if len(addresses) >= count:
                    return addresses
        if len(addresses) < count:
            new_list = [addr for addr in self.new_addresses if self.addresses[addr].should_retry]
            random.shuffle(new_list)
            addresses.extend(new_list[:count - len(addresses)])
        return addresses

    def get_random_address(self) -> Optional[str]:
        if not self.addresses:
            return None
        if self.tried_addresses and random.random() < 0.7:
            return random.choice(list(self.tried_addresses))
        elif self.new_addresses:
            return random.choice(list(self.new_addresses))
        return None

    def get_peers_count(self) -> int:
        return len(self.addresses)

    def get_tried_count(self) -> int:
        return len(self.tried_addresses)

    def get_new_count(self) -> int:
        return len(self.new_addresses)

    def _evict_oldest(self) -> None:
        if not self.addresses:
            return
        oldest = min(self.addresses.values(), key=lambda x: x.last_seen)
        if oldest.address in self.new_addresses:
            self.new_addresses.remove(oldest.address)
        if oldest.address in self.tried_addresses:
            self.tried_addresses.remove(oldest.address)
        del self.addresses[oldest.address]
        logger.debug(f"Evicted oldest address: {oldest.address}")

    def clear(self) -> None:
        self.addresses.clear()
        self.new_addresses.clear()
        self.tried_addresses.clear()
        self.static_peers.clear()
        self.static_peer_priority.clear()
        self.anchor_peers.clear()
        self.save_anchor_peers()

    def get_stats(self) -> Dict[str, int]:
        return {'total': len(self.addresses), 'new': len(self.new_addresses), 'tried': len(self.tried_addresses), 'max': self.max_addresses}
