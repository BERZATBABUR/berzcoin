"""Block index management."""

from typing import List, Optional, Dict, Any, Tuple
from enum import IntEnum
from shared.core.block import Block, BlockHeader
from shared.utils.logging import get_logger
from node.storage.db import Database
from .chainwork import ChainWork

logger = get_logger()

class BlockStatus(IntEnum):
    NONE = 0
    HEADER = 1 << 0
    BLOCK = 1 << 1
    VALID = 1 << 2
    MAIN_CHAIN = 1 << 3
    ORPHAN = 1 << 4

class BlockIndexEntry:
    def __init__(self, height: int, block_hash: str, header: BlockHeader,
                 chainwork: int, status: int = 0):
        self.height = height
        self.block_hash = block_hash
        self.header = header
        self.chainwork = chainwork
        self.status = status

    def has_status(self, flag: BlockStatus) -> bool:
        return (self.status & flag) != 0

    def set_status(self, flag: BlockStatus) -> None:
        self.status |= flag

    def clear_status(self, flag: BlockStatus) -> None:
        self.status &= ~flag

    def is_main_chain(self) -> bool:
        return self.has_status(BlockStatus.MAIN_CHAIN)

    def is_valid(self) -> bool:
        return self.has_status(BlockStatus.VALID)

    def is_orphan(self) -> bool:
        return self.has_status(BlockStatus.ORPHAN)

class BlockIndex:
    def __init__(self, db: Database):
        self.db = db
        self._index: Dict[str, BlockIndexEntry] = {}
        self._height_index: Dict[int, str] = {}
        self._best_height: int = -1
        self._best_hash: Optional[str] = None

    def load(self) -> None:
        results = self.db.fetch_all("""
            SELECT * FROM block_headers 
            ORDER BY height
        """)
        for result in results:
            header = BlockHeader(
                version=result['version'],
                prev_block_hash=bytes.fromhex(result['prev_block_hash']),
                merkle_root=bytes.fromhex(result['merkle_root']),
                timestamp=result['timestamp'],
                bits=result['bits'],
                nonce=result['nonce']
            )
            status = BlockStatus.HEADER
            if result['is_valid']:
                status |= BlockStatus.VALID
            entry = BlockIndexEntry(
                height=result['height'],
                block_hash=result['hash'],
                header=header,
                chainwork=int(result['chainwork']),
                status=status
            )
            self._index[result['hash']] = entry
            self._height_index[result['height']] = result['hash']
            if result['height'] > self._best_height:
                self._best_height = result['height']
                self._best_hash = result['hash']
        logger.info(f"Loaded {len(self._index)} blocks from index")

    def add_block(self, block: Block, height: int, chainwork: int) -> BlockIndexEntry:
        block_hash = block.header.hash_hex()
        entry = BlockIndexEntry(
            height=height,
            block_hash=block_hash,
            header=block.header,
            chainwork=chainwork,
            status=BlockStatus.HEADER | BlockStatus.BLOCK | BlockStatus.VALID
        )
        self._index[block_hash] = entry
        self._height_index[height] = block_hash
        if chainwork > self.get_best_chainwork():
            self._best_height = height
            self._best_hash = block_hash
        logger.debug(f"Added block {block_hash[:16]} at height {height} to index")
        return entry

    def get_block(self, block_hash: str) -> Optional[BlockIndexEntry]:
        return self._index.get(block_hash)

    def get_block_by_height(self, height: int) -> Optional[BlockIndexEntry]:
        block_hash = self._height_index.get(height)
        return self._index.get(block_hash) if block_hash else None

    def get_height(self, block_hash: str) -> Optional[int]:
        entry = self._index.get(block_hash)
        return entry.height if entry else None

    def get_best_height(self) -> int:
        return self._best_height

    def get_best_hash(self) -> Optional[str]:
        return self._best_hash

    def get_best_chainwork(self) -> int:
        if self._best_hash:
            entry = self._index.get(self._best_hash)
            return entry.chainwork if entry else 0
        return 0

    def get_ancestor(self, block_hash: str, height: int) -> Optional[BlockIndexEntry]:
        entry = self._index.get(block_hash)
        if not entry or entry.height < height:
            return None
        while entry and entry.height > height:
            entry = self._index.get(entry.header.prev_block_hash.hex())
        return entry

    def get_chain(self, from_hash: str, to_hash: str) -> List[BlockIndexEntry]:
        chain = []
        current = self._index.get(to_hash)
        while current and current.block_hash != from_hash:
            chain.append(current)
            current = self._index.get(current.header.prev_block_hash.hex())
        if current and current.block_hash == from_hash:
            chain.append(current)
        return list(reversed(chain))

    def find_fork(self, block_hash: str) -> Tuple[Optional[BlockIndexEntry], Optional[BlockIndexEntry]]:
        candidate = self._index.get(block_hash)
        if not candidate:
            return None, None
        best = self._index.get(self._best_hash)
        if not best:
            return None, candidate
        while candidate.height > best.height:
            candidate = self._index.get(candidate.header.prev_block_hash.hex())
        while best.height > candidate.height:
            best = self._index.get(best.header.prev_block_hash.hex())
        while candidate and best and candidate.block_hash != best.block_hash:
            candidate = self._index.get(candidate.header.prev_block_hash.hex())
            best = self._index.get(best.header.prev_block_hash.hex())
        return best, self._index.get(block_hash)

    def mark_main_chain(self, block_hash: str, is_main: bool = True) -> None:
        entry = self._index.get(block_hash)
        if entry:
            if is_main:
                entry.set_status(BlockStatus.MAIN_CHAIN)
            else:
                entry.clear_status(BlockStatus.MAIN_CHAIN)

    def size(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        self._index.clear()
        self._height_index.clear()
        self._best_height = -1
        self._best_hash = None
