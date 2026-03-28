"""Block header chain management."""

from typing import List, Optional, Dict, Any, Tuple
from shared.core.block import BlockHeader
from shared.core.hashes import hash256
from shared.utils.logging import get_logger
from node.storage.db import Database
from node.storage.blocks_store import BlocksStore

logger = get_logger()

class HeaderChain:
    """Block header chain manager."""
    
    def __init__(self, db: Database, blocks_store: BlocksStore):
        self.db = db
        self.blocks_store = blocks_store
        self._header_cache: Dict[int, BlockHeader] = {}
        self._height_cache: Dict[str, int] = {}
    
    def add_header(self, header: BlockHeader, height: int, chainwork: int) -> None:
        block_hash = header.hash_hex()
        self.db.execute("""
            INSERT OR REPLACE INTO block_headers
            (hash, height, version, prev_block_hash, merkle_root,
             timestamp, bits, nonce, chainwork, is_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            block_hash, height, header.version,
            header.prev_block_hash.hex(),
            header.merkle_root.hex(),
            header.timestamp, header.bits, header.nonce,
            str(chainwork), True
        ))
        self._header_cache[height] = header
        self._height_cache[block_hash] = height
        logger.debug(f"Added header {block_hash[:16]} at height {height}")
    
    def get_header(self, height: int) -> Optional[BlockHeader]:
        if height in self._header_cache:
            return self._header_cache[height]
        result = self.db.fetch_one(
            """
            SELECT * FROM block_headers
            WHERE height = ?
            ORDER BY CAST(chainwork AS INTEGER) DESC
            LIMIT 1
            """,
            (height,),
        )
        if not result:
            return None
        header = BlockHeader(
            version=result['version'],
            prev_block_hash=bytes.fromhex(result['prev_block_hash']),
            merkle_root=bytes.fromhex(result['merkle_root']),
            timestamp=result['timestamp'],
            bits=result['bits'],
            nonce=result['nonce']
        )
        self._header_cache[height] = header
        return header
    
    def get_header_by_hash(self, block_hash: str) -> Optional[BlockHeader]:
        if block_hash in self._height_cache:
            return self.get_header(self._height_cache[block_hash])
        result = self.db.fetch_one("SELECT * FROM block_headers WHERE hash = ?", (block_hash,))
        if not result:
            return None
        header = BlockHeader(
            version=result['version'],
            prev_block_hash=bytes.fromhex(result['prev_block_hash']),
            merkle_root=bytes.fromhex(result['merkle_root']),
            timestamp=result['timestamp'],
            bits=result['bits'],
            nonce=result['nonce']
        )
        self._header_cache[result['height']] = header
        self._height_cache[block_hash] = result['height']
        return header
    
    def get_height(self, block_hash: str) -> Optional[int]:
        if block_hash in self._height_cache:
            return self._height_cache[block_hash]
        result = self.db.fetch_one("SELECT height FROM block_headers WHERE hash = ?", (block_hash,))
        if result:
            self._height_cache[block_hash] = result['height']
            return result['height']
        return None
    
    def get_best_height(self) -> int:
        result = self.db.fetch_one("SELECT MAX(height) as max_height FROM block_headers WHERE is_valid = 1")
        return result['max_height'] if result and result['max_height'] else -1
    
    def get_best_header(self) -> Optional[BlockHeader]:
        height = self.get_best_height()
        return self.get_header(height) if height >= 0 else None
    
    def get_headers_range(self, start_height: int, count: int) -> List[BlockHeader]:
        headers = []
        for height in range(start_height, start_height + count):
            header = self.get_header(height)
            if not header:
                continue
            headers.append(header)
        return headers
    
    def get_last_headers(self, count: int) -> List[BlockHeader]:
        headers = []
        best_height = self.get_best_height()
        if best_height < 0:
            return headers
        start = max(0, best_height - count + 1)
        for h in range(start, best_height + 1):
            header = self.get_header(h)
            if header:
                headers.append(header)
        return headers
    
    def find_fork_point(self, headers: List[BlockHeader]) -> Optional[int]:
        for header in headers:
            existing_height = self.get_height(header.hash_hex())
            if existing_height is not None:
                existing_header = self.get_header(existing_height)
                if existing_header and existing_header.hash() == header.hash():
                    return existing_height
        return None
    
    def header_exists(self, block_hash: str) -> bool:
        return self.get_height(block_hash) is not None
    
    def validate_parent(self, header: BlockHeader) -> bool:
        if header.prev_block_hash == b'' * 32:
            return True
        return self.header_exists(header.prev_block_hash.hex())
    
    def get_chainwork(self, height: int) -> int:
        result = self.db.fetch_one(
            """
            SELECT chainwork FROM block_headers
            WHERE height = ?
            ORDER BY CAST(chainwork AS INTEGER) DESC
            LIMIT 1
            """,
            (height,),
        )
        return int(result['chainwork']) if result else 0
    
    def clear_cache(self) -> None:
        self._header_cache.clear()
        self._height_cache.clear()
