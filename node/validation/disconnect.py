"""Block disconnection logic."""

from typing import List, Dict, Any
from shared.core.block import Block
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from node.chain.block_index import BlockIndex

logger = get_logger()

class DisconnectBlock:
    """Disconnect block from chain."""
    
    def __init__(self, utxo_store: UTXOStore, block_index: BlockIndex):
        self.utxo_store = utxo_store
        self.block_index = block_index
    
    def disconnect(self, block: Block) -> bool:
        height = self.block_index.get_height(block.header.hash_hex())
        if height is None:
            logger.error("Block not in index")
            return False
        logger.debug(f"Disconnecting block {height}")
        try:
            with self.utxo_store.db.transaction():
                for tx in reversed(block.transactions):
                    if not tx.is_coinbase():
                        for txin in tx.vin:
                            original = self.utxo_store.db.fetch_one("""
                                SELECT * FROM outputs
                                WHERE txid = ? AND "index" = ?
                            """, (txin.prev_tx_hash.hex(), txin.prev_tx_index))
                            if original:
                                self.utxo_store.add_utxo(
                                    txid=txin.prev_tx_hash.hex(),
                                    index=txin.prev_tx_index,
                                    value=original['value'],
                                    script_pubkey=original['script_pubkey'],
                                    height=original['height'],
                                    is_coinbase=False
                                )
                    for i in range(len(tx.vout)):
                        self.utxo_store.spend_utxo(tx.txid().hex(), i)
                self.block_index.mark_main_chain(block.header.hash_hex(), False)
                prev_hash = block.header.prev_block_hash.hex()
                prev_entry = self.block_index.get_block(prev_hash)
                if prev_entry:
                    self.utxo_store.db.execute("""
                        UPDATE chain_state SET best_hash = ?, best_height = ?, best_chainwork = ?
                        WHERE id = 1
                    """, (prev_hash, prev_entry.height, prev_entry.chainwork))
            logger.debug(f"Block {height} disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to disconnect block: {e}")
            return False
    
    def disconnect_until_height(self, target_height: int) -> List[Block]:
        disconnected = []
        best_hash = self.block_index.get_best_hash()
        if not best_hash:
            return disconnected
        current = self.block_index.get_block(best_hash)
        while current and current.height > target_height:
            block = None
            if block and self.disconnect(block):
                disconnected.append(block)
            current = self.block_index.get_block(current.header.prev_block_hash.hex())
        return disconnected
    
    def disconnect_block_range(self, start_height: int, end_height: int) -> List[Block]:
        disconnected = []
        for height in range(start_height, end_height - 1, -1):
            entry = self.block_index.get_block_by_height(height)
            if entry:
                pass
        return disconnected
    
    def rollback_utxos(self, block_hash: str) -> bool:
        entry = self.block_index.get_block(block_hash)
        if not entry:
            logger.error(f"Block {block_hash} not found")
            return False
        current = self.block_index.get_best_hash()
        blocks_to_disconnect = []
        current_entry = self.block_index.get_block(current)
        while current_entry and current_entry.height > entry.height:
            blocks_to_disconnect.append(current_entry.block_hash)
            current_entry = self.block_index.get_block(current_entry.header.prev_block_hash.hex())
        for block_hash in blocks_to_disconnect:
            pass
        return True
