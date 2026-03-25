"""DNS seed resolution for peer discovery."""

import asyncio
import random
import socket
from typing import List, Optional
from shared.utils.logging import get_logger

logger = get_logger()

class DNSSeeds:
    DEFAULT_SEEDS = [
        "seed.berzcoin.sipa.be",
        "dnsseed.berzcoin.dashjr.org",
        "seed.berzcoin.launchpad.net",
        "seed.berzcoin.bitcoinperu.com",
        "seed.berzcoin.obliquetech.com",
    ]

    def __init__(self, seeds: List[str] = None, timeout: int = 5):
        self.seeds = seeds or self.DEFAULT_SEEDS
        self.timeout = timeout
        self.cache: List[str] = []
        self.last_refresh = 0
        self.cache_ttl = 3600

    async def get_seeds(self, force_refresh: bool = False) -> List[str]:
        import time
        now = time.time()
        if not force_refresh and self.cache and now - self.last_refresh < self.cache_ttl:
            return self.cache
        addresses = set()
        for seed in self.seeds:
            try:
                seed_addresses = await self._resolve_seed(seed)
                addresses.update(seed_addresses)
                logger.debug(f"Resolved {len(seed_addresses)} addresses from {seed}")
            except Exception as e:
                logger.warning(f"Failed to resolve seed {seed}: {e}")
        result = list(addresses)
        random.shuffle(result)
        self.cache = result
        self.last_refresh = now
        logger.info(f"Resolved {len(result)} seed addresses")
        return result

    async def _resolve_seed(self, seed: str) -> List[str]:
        addresses = []
        try:
            loop = asyncio.get_event_loop()
            ips = await loop.getaddrinfo(seed, 8333, proto=socket.IPPROTO_TCP)
            seen = set()
            for addr in ips:
                ip = addr[4][0]
                if ip not in seen:
                    seen.add(ip)
                    addresses.append(f"{ip}:8333")
        except asyncio.TimeoutError:
            logger.debug(f"Timeout resolving {seed}")
        except Exception as e:
            logger.debug(f"Error resolving {seed}: {e}")
        return addresses

    async def get_one_seed(self) -> Optional[str]:
        seeds = await self.get_seeds()
        return seeds[0] if seeds else None

    def add_seed(self, seed: str) -> None:
        if seed not in self.seeds:
            self.seeds.append(seed)
            self.cache = []

    def remove_seed(self, seed: str) -> None:
        if seed in self.seeds:
            self.seeds.remove(seed)
            self.cache = []

    def get_seed_count(self) -> int:
        return len(self.seeds)

    def clear_cache(self) -> None:
        self.cache = []
        self.last_refresh = 0
        logger.debug("Cleared DNS seed cache")
