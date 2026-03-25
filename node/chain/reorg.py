"""Blockchain reorganization logic."""

from typing import List, Tuple, Optional
from shared.core.block import Block
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from .block_index import BlockIndex, BlockIndexEntry
from node.validation.connect import ConnectBlock
from node.validation.disconnect import DisconnectBlock

logger = get_logger()

class ReorgManager:
    """Blockchain reorganization manager."""
    
    def __init__(self, utxo_store: UTXOStore, block_index: BlockIndex):
        self.utxo_store = utxo_store
        self.block_index = block_index
        self.connect_block = ConnectBlock(utxo_store, block_index)
        self.disconnect_block = DisconnectBlock(utxo_store, block_index)
    
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

        disconnected: List[Block] = []
        connected: List[Block] = []

        try:
            # Disconnect blocks from old chain (down to fork point).
            current = old_best_block
            while current and current.height > fork_point.height:
                block = get_block_func(current.block_hash)
                if not block:
                    raise RuntimeError(f"Failed to get block {current.block_hash}")
                if not self.disconnect_block.disconnect(block):
                    raise RuntimeError(f"Failed to disconnect block {current.height}")
                disconnected.append(block)
                current = self.block_index.get_block(
                    current.header.prev_block_hash.hex()
                )

            # Connect blocks from new chain (from fork point upwards).
            to_connect: List[BlockIndexEntry] = []
            current = new_best_block
            while current and current.height > fork_point.height:
                to_connect.insert(0, current)
                current = self.block_index.get_block(
                    current.header.prev_block_hash.hex()
                )

            for entry in to_connect:
                block = get_block_func(entry.block_hash)
                if not block:
                    raise RuntimeError(f"Failed to get block {entry.block_hash}")
                if not self.connect_block.connect(block):
                    raise RuntimeError(f"Failed to connect block {entry.height}")
                connected.append(block)
                self.block_index.mark_main_chain(entry.block_hash, True)

            logger.info(
                "Reorg complete: disconnected %s, connected %s",
                len(disconnected),
                len(connected),
            )
            return True, disconnected, connected

        except Exception as e:
            logger.error("Reorg failed: %s; rolling back...", e)

            # Roll back newly-connected blocks.
            for block in reversed(connected):
                self.disconnect_block.disconnect(block)

            # Reconnect previously-disconnected blocks.
            for block in reversed(disconnected):
                self.connect_block.connect(block)

            return False, [], []
    
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
