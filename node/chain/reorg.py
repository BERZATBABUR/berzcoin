"""Blockchain reorganization logic."""

from contextlib import nullcontext
from typing import Dict, List, Tuple, Optional, ContextManager
from shared.core.block import Block
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from .block_index import BlockIndex, BlockIndexEntry
from node.validation.connect import ConnectBlock
from node.validation.disconnect import DisconnectBlock

logger = get_logger()

class ReorgManager:
    """Blockchain reorganization manager."""
    
    def __init__(
        self,
        utxo_store: UTXOStore,
        block_index: BlockIndex,
        max_reorg_depth: int = 144,
    ):
        self.utxo_store = utxo_store
        self.block_index = block_index
        self.connect_block = ConnectBlock(utxo_store, block_index)
        self.disconnect_block = DisconnectBlock(utxo_store, block_index)
        self.max_reorg_depth = max_reorg_depth
    
    def reorganize(
        self,
        new_best_block: BlockIndexEntry,
        old_best_block: BlockIndexEntry,
        get_block_func,
    ) -> Tuple[bool, List[Block], List[Block]]:
        """Perform blockchain reorganization with rollback on failure.

        Note: `ConnectBlock` / `DisconnectBlock` are synchronous in this codebase,
        so this method remains synchronous as well.
        """
        logger.info(
            "Reorganization from height %s to %s",
            old_best_block.height,
            new_best_block.height,
        )

        fork_point = self._find_common_ancestor(new_best_block, old_best_block)
        if not fork_point:
            logger.error("No common ancestor found")
            return False, [], []
        logger.info("Fork point at height %s", fork_point.height)

        disconnect_depth = old_best_block.height - fork_point.height
        if disconnect_depth > self.max_reorg_depth:
            logger.error(
                "Reorg depth %s exceeds safety limit %s",
                disconnect_depth,
                self.max_reorg_depth,
            )
            return False, [], []

        status_snapshot: Dict[str, Optional[int]] = {}
        try:
            # Build disconnect path (tip -> fork).
            disconnect_entries: List[BlockIndexEntry] = []
            current = old_best_block
            while current and current.height > fork_point.height:
                disconnect_entries.append(current)
                current = self.block_index.get_block(
                    current.header.prev_block_hash.hex()
                )

            # Build connect path (fork -> tip, excluding fork).
            connect_entries: List[BlockIndexEntry] = []
            current = new_best_block
            while current and current.height > fork_point.height:
                connect_entries.append(current)
                current = self.block_index.get_block(
                    current.header.prev_block_hash.hex()
                )
            connect_entries.reverse()

            self._validate_reorg_paths(fork_point, disconnect_entries, connect_entries)

            touched_hashes = [e.block_hash for e in disconnect_entries + connect_entries]
            status_snapshot = self._snapshot_statuses(touched_hashes)
            disconnected: List[Block] = []
            connected: List[Block] = []

            with self._transaction():
                for entry in disconnect_entries:
                    block = get_block_func(entry.block_hash)
                    if not block:
                        raise RuntimeError(f"Failed to get block {entry.block_hash}")
                    if not self.disconnect_block.disconnect(block):
                        raise RuntimeError(f"Failed to disconnect block {entry.height}")
                    disconnected.append(block)
                    self.block_index.mark_main_chain(entry.block_hash, False)

                for entry in connect_entries:
                    block = get_block_func(entry.block_hash)
                    if not block:
                        raise RuntimeError(f"Failed to get block {entry.block_hash}")
                    if not self.connect_block.connect(block):
                        raise RuntimeError(f"Failed to connect block {entry.height}")
                    connected.append(block)
                    self.block_index.mark_main_chain(entry.block_hash, True)

                self._assert_post_reorg_invariants(disconnect_entries, connect_entries)
                # Rebuild active-chain height map and best tip in one place.
                self.block_index.set_best_chain_tip(new_best_block.block_hash)

            logger.info(
                "Reorg complete: disconnected %s, connected %s",
                len(disconnected),
                len(connected),
            )
            return True, disconnected, connected

        except Exception as e:
            logger.error("Reorg failed: %s; rolling back...", e)
            try:
                self._restore_statuses(status_snapshot)
                self.block_index.set_best_chain_tip(old_best_block.block_hash)
            except Exception:
                pass
            return False, [], []

    def can_reorganize(
        self,
        new_best_block: BlockIndexEntry,
        old_best_block: BlockIndexEntry,
        get_block_func,
    ) -> bool:
        """Preflight reorg by simulating disconnect/connect without committing state."""
        fork_point = self._find_common_ancestor(new_best_block, old_best_block)
        if not fork_point:
            return False

        disconnect_depth = old_best_block.height - fork_point.height
        if disconnect_depth > self.max_reorg_depth:
            return False

        disconnect_entries: List[BlockIndexEntry] = []
        current = old_best_block
        while current and current.height > fork_point.height:
            disconnect_entries.append(current)
            current = self.block_index.get_block(current.header.prev_block_hash.hex())

        connect_entries: List[BlockIndexEntry] = []
        current = new_best_block
        while current and current.height > fork_point.height:
            connect_entries.append(current)
            current = self.block_index.get_block(current.header.prev_block_hash.hex())
        connect_entries.reverse()

        try:
            self._validate_reorg_paths(fork_point, disconnect_entries, connect_entries)
        except Exception:
            return False

        touched_hashes = [e.block_hash for e in disconnect_entries + connect_entries]
        status_snapshot = self._snapshot_statuses(touched_hashes)
        original_best_hash = self.block_index.get_best_hash()

        db = getattr(self.utxo_store, "db", None)
        conn = getattr(db, "connection", None)
        if conn is None:
            try:
                for entry in disconnect_entries:
                    block = get_block_func(entry.block_hash)
                    if not block or not self.disconnect_block.disconnect(block):
                        return False
                    self.block_index.mark_main_chain(entry.block_hash, False)
                for entry in connect_entries:
                    block = get_block_func(entry.block_hash)
                    if not block or not self.connect_block.connect(block):
                        return False
                    self.block_index.mark_main_chain(entry.block_hash, True)
                return True
            finally:
                self._restore_statuses(status_snapshot)
                if original_best_hash:
                    self.block_index.set_best_chain_tip(original_best_hash)

        savepoint = "reorg_preflight"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            for entry in disconnect_entries:
                block = get_block_func(entry.block_hash)
                if not block or not self.disconnect_block.disconnect(block):
                    return False
                self.block_index.mark_main_chain(entry.block_hash, False)

            for entry in connect_entries:
                block = get_block_func(entry.block_hash)
                if not block or not self.connect_block.connect(block):
                    return False
                self.block_index.mark_main_chain(entry.block_hash, True)
            return True
        finally:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            finally:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                self._restore_statuses(status_snapshot)
                if original_best_hash:
                    self.block_index.set_best_chain_tip(original_best_hash)
    
    def _find_common_ancestor(self, block1: BlockIndexEntry, block2: BlockIndexEntry) -> Optional[BlockIndexEntry]:
        while block1.height > block2.height:
            block1 = self.block_index.get_block(block1.header.prev_block_hash.hex())
            if not block1:
                return None
        while block2.height > block1.height:
            block2 = self.block_index.get_block(block2.header.prev_block_hash.hex())
            if not block2:
                return None
        while block1 and block2 and block1.block_hash != block2.block_hash:
            block1 = self.block_index.get_block(block1.header.prev_block_hash.hex())
            block2 = self.block_index.get_block(block2.header.prev_block_hash.hex())
        return block1 if block1 and block2 else None
    
    def would_reorganize(self, new_block: BlockIndexEntry, current_best: BlockIndexEntry) -> bool:
        return new_block.chainwork > current_best.chainwork

    def _transaction(self) -> ContextManager:
        db = getattr(self.utxo_store, "db", None)
        tx = getattr(db, "transaction", None)
        if callable(tx):
            return tx()
        return nullcontext()

    def _snapshot_statuses(self, block_hashes: List[str]) -> Dict[str, Optional[int]]:
        snapshot: Dict[str, Optional[int]] = {}
        for block_hash in block_hashes:
            entry = self.block_index.get_block(block_hash)
            snapshot[block_hash] = getattr(entry, "status", None) if entry else None
        return snapshot

    def _restore_statuses(self, snapshot: Dict[str, Optional[int]]) -> None:
        for block_hash, status in snapshot.items():
            entry = self.block_index.get_block(block_hash)
            if entry is None or status is None or not hasattr(entry, "status"):
                continue
            entry.status = status

    def _validate_reorg_paths(
        self,
        fork_point: BlockIndexEntry,
        disconnect_entries: List[BlockIndexEntry],
        connect_entries: List[BlockIndexEntry],
    ) -> None:
        prev = None
        for entry in disconnect_entries:
            if prev is not None and entry.block_hash != prev.header.prev_block_hash.hex():
                raise RuntimeError("Disconnect path invariant failed")
            prev = entry

        expected_parent = fork_point.block_hash
        for entry in connect_entries:
            actual_parent = entry.header.prev_block_hash.hex()
            if actual_parent != expected_parent:
                raise RuntimeError("Connect path invariant failed")
            expected_parent = entry.block_hash

    def _assert_post_reorg_invariants(
        self,
        disconnect_entries: List[BlockIndexEntry],
        connect_entries: List[BlockIndexEntry],
    ) -> None:
        for entry in disconnect_entries:
            current = self.block_index.get_block(entry.block_hash)
            if current and current.is_main_chain():
                raise RuntimeError("Disconnected block still marked main-chain")
        for entry in connect_entries:
            current = self.block_index.get_block(entry.block_hash)
            if current and not current.is_main_chain():
                raise RuntimeError("Connected block not marked main-chain")
