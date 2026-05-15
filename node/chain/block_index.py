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
    SIDE_CHAIN = 1 << 5
    INVALID = 1 << 6
    DISCONNECTED = 1 << 7
    FAILED_VALIDATION = 1 << 8

class BlockIndexEntry:
    def __init__(self, height: int, block_hash: str, header: BlockHeader,
                 chainwork: int, status: int = 0, raw_meta: Optional[Dict[str, Any]] = None):
        self.height = height
        self.block_hash = block_hash
        self.header = header
        self.chainwork = chainwork
        self.status = status
        self.raw_meta = raw_meta or {}

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

    def is_invalid(self) -> bool:
        return self.has_status(BlockStatus.INVALID) or self.has_status(BlockStatus.FAILED_VALIDATION)

class BlockIndex:
    def __init__(self, db: Database):
        self.db = db
        self._index: Dict[str, BlockIndexEntry] = {}
        self._height_index: Dict[int, str] = {}
        self._best_height: int = -1
        self._best_hash: Optional[str] = None
        self._invalid_reasons: Dict[str, str] = {}

    def load(self) -> None:
        results = self.db.fetch_all("""
            SELECT h.*, b.file_path, b.file_number, b.file_offset, b.size AS raw_size
            FROM block_headers h
            LEFT JOIN blocks b ON b.hash = h.hash
            ORDER BY height
        """)
        best_by_work: Optional[BlockIndexEntry] = None
        for result in results:
            try:
                chainwork = int(result['chainwork'])
            except Exception:
                logger.error("Corrupted chainwork for block %s: %r", result.get("hash", "")[:16], result.get("chainwork"))
                chainwork = -1
            header = BlockHeader(
                version=result['version'],
                prev_block_hash=bytes.fromhex(result['prev_block_hash']),
                merkle_root=bytes.fromhex(result['merkle_root']),
                timestamp=result['timestamp'],
                bits=result['bits'],
                nonce=result['nonce']
            )
            status = int(result.get('status_flags') or BlockStatus.HEADER)
            if int(result.get('is_valid', 0)):
                status |= BlockStatus.VALID
            else:
                status |= BlockStatus.INVALID
            if chainwork < 0:
                status |= BlockStatus.INVALID | BlockStatus.FAILED_VALIDATION
            entry = BlockIndexEntry(
                height=result['height'],
                block_hash=result['hash'],
                header=header,
                chainwork=max(0, chainwork),
                status=status,
                raw_meta={
                    "file_path": result.get("file_path"),
                    "file_number": result.get("file_number"),
                    "file_offset": result.get("file_offset"),
                    "size": result.get("raw_size"),
                },
            )
            self._index[result['hash']] = entry
            if entry.is_invalid():
                self._invalid_reasons[result['hash']] = "persisted-invalid-or-corrupt"
                continue
            if best_by_work is None or entry.chainwork > best_by_work.chainwork:
                best_by_work = entry
            elif best_by_work is not None and entry.chainwork == best_by_work.chainwork and entry.height > best_by_work.height:
                best_by_work = entry
        if best_by_work is not None:
            self._best_height = best_by_work.height
            self._best_hash = best_by_work.block_hash
            self._rebuild_main_chain_height_index(best_by_work.block_hash)
        logger.info(f"Loaded {len(self._index)} blocks from index")

    def add_block(
        self,
        block: Block,
        height: int,
        chainwork: int,
        update_best: bool = True,
    ) -> BlockIndexEntry:
        block_hash = block.header.hash_hex()
        entry = BlockIndexEntry(
            height=height,
            block_hash=block_hash,
            header=block.header,
            chainwork=chainwork,
            status=BlockStatus.HEADER | BlockStatus.BLOCK | BlockStatus.VALID
        )
        parent_hash = block.header.prev_block_hash.hex()
        parent = self._index.get(parent_hash)
        if parent is None and height > 0:
            entry.set_status(BlockStatus.ORPHAN)
            entry.set_status(BlockStatus.SIDE_CHAIN)
        elif parent and parent.is_invalid():
            entry.set_status(BlockStatus.INVALID)
            entry.set_status(BlockStatus.FAILED_VALIDATION)
            self._invalid_reasons[block_hash] = "parent-invalid"
        self._index[block_hash] = entry
        if update_best and not entry.is_invalid() and chainwork > self.get_best_chainwork():
            self.set_best_chain_tip(block_hash)
        elif self._best_hash and block_hash != self._best_hash:
            entry.set_status(BlockStatus.SIDE_CHAIN)
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
                entry.clear_status(BlockStatus.SIDE_CHAIN)
                entry.clear_status(BlockStatus.DISCONNECTED)
                self._height_index[entry.height] = block_hash
            else:
                entry.clear_status(BlockStatus.MAIN_CHAIN)
                entry.set_status(BlockStatus.SIDE_CHAIN)
                entry.set_status(BlockStatus.DISCONNECTED)
                if self._height_index.get(entry.height) == block_hash:
                    del self._height_index[entry.height]
            self._persist_status(entry)

    def set_best_chain_tip(self, block_hash: str) -> None:
        """Select tip and rebuild main-chain status/height map from parent links."""
        tip = self._index.get(block_hash)
        if tip is None:
            return
        if tip.is_invalid() or self.is_branch_invalid(block_hash):
            logger.warning("Ignoring invalid tip candidate %s", block_hash[:16])
            return
        self._best_hash = tip.block_hash
        self._best_height = tip.height
        self._rebuild_main_chain_height_index(block_hash)

    def _rebuild_main_chain_height_index(self, tip_hash: str) -> None:
        chain_hashes = set()
        chain_map: Dict[int, str] = {}
        current = self._index.get(tip_hash)
        while current is not None:
            chain_hashes.add(current.block_hash)
            chain_map[current.height] = current.block_hash
            current = self._index.get(current.header.prev_block_hash.hex())

        # Clear all main-chain flags then set for active chain.
        for entry in self._index.values():
            if entry.block_hash in chain_hashes:
                entry.set_status(BlockStatus.MAIN_CHAIN)
                entry.clear_status(BlockStatus.SIDE_CHAIN)
            else:
                entry.clear_status(BlockStatus.MAIN_CHAIN)
                entry.set_status(BlockStatus.SIDE_CHAIN)
            self._persist_status(entry)

        self._height_index = chain_map

    def mark_invalid(self, block_hash: str, reason: str = "") -> None:
        entry = self._index.get(block_hash)
        if entry:
            entry.set_status(BlockStatus.INVALID)
            entry.set_status(BlockStatus.FAILED_VALIDATION)
            entry.clear_status(BlockStatus.MAIN_CHAIN)
            entry.set_status(BlockStatus.SIDE_CHAIN)
            self._invalid_reasons[block_hash] = reason or "validation-failed"
            self._persist_status(entry)
            if self._best_hash == block_hash:
                self._reselect_best_tip()

    def get_invalid_reason(self, block_hash: str) -> Optional[str]:
        return self._invalid_reasons.get(block_hash)

    def is_known_invalid(self, block_hash: str) -> bool:
        entry = self._index.get(block_hash)
        return bool(entry and entry.is_invalid())

    def is_branch_invalid(self, block_hash: str) -> bool:
        current = self._index.get(block_hash)
        while current is not None:
            if current.is_invalid():
                return True
            current = self._index.get(current.header.prev_block_hash.hex())
        return False

    def validate_consistency(self, blocks_store=None) -> Dict[str, Any]:
        issues: List[str] = []
        if self._best_hash and self._best_hash not in self._index:
            issues.append("best_tip_missing_from_index")
        for block_hash, entry in self._index.items():
            if entry.height > 0 and entry.header.prev_block_hash.hex() not in self._index:
                issues.append(f"missing_parent:{block_hash}")
            if entry.chainwork < 0:
                issues.append(f"corrupted_chainwork:{block_hash}")
            h = self._height_index.get(entry.height)
            if entry.is_main_chain() and h != block_hash:
                issues.append(f"height_index_mismatch:{entry.height}:{block_hash}")
            if blocks_store and entry.raw_meta.get("file_path"):
                block = blocks_store.read_block_by_hash(block_hash)
                if block is None:
                    issues.append(f"missing_or_corrupt_raw_block:{block_hash}")
        return {"ok": len(issues) == 0, "issues": issues}

    def size(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        self._index.clear()
        self._height_index.clear()
        self._best_height = -1
        self._best_hash = None
        self._invalid_reasons.clear()

    def _persist_status(self, entry: BlockIndexEntry) -> None:
        self.db.execute(
            "UPDATE block_headers SET is_valid = ?, status_flags = ? WHERE hash = ?",
            (0 if entry.is_invalid() else 1, int(entry.status), entry.block_hash),
        )

    def _reselect_best_tip(self) -> None:
        candidates = [e for e in self._index.values() if not e.is_invalid() and not self.is_branch_invalid(e.block_hash)]
        if not candidates:
            self._best_hash = None
            self._best_height = -1
            self._height_index = {}
            return
        best = max(candidates, key=lambda e: (e.chainwork, e.height))
        self.set_best_chain_tip(best.block_hash)
