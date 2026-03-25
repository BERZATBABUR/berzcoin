"""Orphan block management."""

import time
from typing import Dict, List, Optional, Set
from shared.core.block import Block
from shared.utils.logging import get_logger

logger = get_logger()

class OrphanBlock:
    def __init__(self, block: Block, received_at: float):
        self.block = block
        self.received_at = received_at
        self.parent_hash = block.header.prev_block_hash.hex()
        self.block_hash = block.header.hash_hex()
        self.children: Set[str] = set()

class Orphanage:
    def __init__(self, max_orphans: int = 100, max_age: int = 3600):
        self.max_orphans = max_orphans
        self.max_age = max_age
        self.orphans: Dict[str, OrphanBlock] = {}
        self.parent_map: Dict[str, Set[str]] = {}

    def add_orphan(self, block: Block) -> bool:
        block_hash = block.header.hash_hex()
        parent_hash = block.header.prev_block_hash.hex()
        if block_hash in self.orphans:
            return False
        if len(self.orphans) >= self.max_orphans:
            self._evict_oldest()
        orphan = OrphanBlock(block, time.time())
        self.orphans[block_hash] = orphan
        if parent_hash not in self.parent_map:
            self.parent_map[parent_hash] = set()
        self.parent_map[parent_hash].add(block_hash)
        logger.debug(f"Added orphan block {block_hash[:16]}, parent {parent_hash[:16]}")
        return True

    def get_orphan(self, block_hash: str) -> Optional[Block]:
        orphan = self.orphans.get(block_hash)
        return orphan.block if orphan else None

    def remove_orphan(self, block_hash: str) -> Optional[Block]:
        orphan = self.orphans.pop(block_hash, None)
        if not orphan:
            return None
        parent_hash = orphan.parent_hash
        if parent_hash in self.parent_map:
            self.parent_map[parent_hash].discard(block_hash)
            if not self.parent_map[parent_hash]:
                del self.parent_map[parent_hash]
        logger.debug(f"Removed orphan block {block_hash[:16]}")
        return orphan.block

    def get_children(self, block_hash: str) -> List[Block]:
        child_hashes = self.parent_map.get(block_hash, set())
        return [self.orphans[h].block for h in child_hashes if h in self.orphans]

    def has_orphan(self, block_hash: str) -> bool:
        return block_hash in self.orphans

    def has_parent(self, block_hash: str) -> bool:
        orphan = self.orphans.get(block_hash)
        if not orphan:
            return False
        return orphan.parent_hash in self.orphans

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [h for h, o in self.orphans.items() if now - o.received_at > self.max_age]
        for h in expired:
            self.remove_orphan(h)
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired orphans")
        return len(expired)

    def _evict_oldest(self) -> None:
        if not self.orphans:
            return
        oldest = min(self.orphans.items(), key=lambda x: x[1].received_at)
        self.remove_orphan(oldest[0])

    def size(self) -> int:
        return len(self.orphans)

    def clear(self) -> None:
        self.orphans.clear()
        self.parent_map.clear()
        logger.debug("Cleared all orphans")
