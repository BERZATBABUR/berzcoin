"""Block disconnection logic."""

from typing import Callable, List, Optional
from shared.core.block import Block
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from node.chain.block_index import BlockIndex

logger = get_logger()

class DisconnectBlock:
    """Disconnect block from chain."""
    
    def __init__(
        self,
        utxo_store: UTXOStore,
        block_index: BlockIndex,
        get_block_func: Optional[Callable[[str], Optional[Block]]] = None,
    ):
        self.utxo_store = utxo_store
        self.block_index = block_index
        self._get_block_func = get_block_func
    
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
                                SELECT o.value, o.script_pubkey, t.height, t.is_coinbase
                                FROM outputs o
                                JOIN transactions t ON t.txid = o.txid
                                WHERE o.txid = ? AND o."index" = ?
                            """, (txin.prev_tx_hash.hex(), txin.prev_tx_index))
                            if not original:
                                logger.error(
                                    "Missing previous output while disconnecting %s:%s",
                                    txin.prev_tx_hash.hex(),
                                    txin.prev_tx_index,
                                )
                                return False
                            self.utxo_store.add_utxo(
                                txid=txin.prev_tx_hash.hex(),
                                index=txin.prev_tx_index,
                                value=int(original["value"]),
                                script_pubkey=bytes(original["script_pubkey"]),
                                height=int(original["height"]),
                                is_coinbase=bool(original["is_coinbase"]),
                            )
                            self.utxo_store.db.execute("""
                                UPDATE outputs
                                SET spent = 0, spent_by_txid = NULL, spent_by_index = NULL
                                WHERE txid = ? AND "index" = ?
                            """, (txin.prev_tx_hash.hex(), txin.prev_tx_index))
                    for i in range(len(tx.vout)):
                        self.utxo_store.remove_utxo(tx.txid().hex(), i)
                        self.utxo_store.db.execute("""
                            UPDATE outputs
                            SET spent = 0, spent_by_txid = NULL, spent_by_index = NULL
                            WHERE txid = ? AND "index" = ?
                        """, (tx.txid().hex(), i))
                self.block_index.mark_main_chain(block.header.hash_hex(), False)
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
            block = self._get_block(current.block_hash)
            if not block:
                logger.error("Missing block data for %s during disconnect_until_height", current.block_hash)
                break
            if not self.disconnect(block):
                break
            disconnected.append(block)
            current = self.block_index.get_block(current.header.prev_block_hash.hex())
        if current and disconnected:
            self.block_index.set_best_chain_tip(current.block_hash)
        return disconnected
    
    def disconnect_block_range(self, start_height: int, end_height: int) -> List[Block]:
        disconnected = []
        if start_height < end_height:
            return disconnected
        for height in range(start_height, end_height - 1, -1):
            entry = self.block_index.get_block_by_height(height)
            if not entry:
                continue
            block = self._get_block(entry.block_hash)
            if not block:
                logger.error("Missing block data for %s at height %s", entry.block_hash, height)
                break
            if not self.disconnect(block):
                break
            disconnected.append(block)
        if disconnected:
            prev_hash = disconnected[-1].header.prev_block_hash.hex()
            prev_entry = self.block_index.get_block(prev_hash)
            if prev_entry:
                self.block_index.set_best_chain_tip(prev_entry.block_hash)
        return disconnected
    
    def rollback_utxos(self, block_hash: str) -> bool:
        entry = self.block_index.get_block(block_hash)
        if not entry:
            logger.error(f"Block {block_hash} not found")
            return False
        current_hash = self.block_index.get_best_hash()
        if not current_hash:
            return False
        current_entry = self.block_index.get_block(current_hash)
        while current_entry and current_entry.height > entry.height:
            block = self._get_block(current_entry.block_hash)
            if not block:
                logger.error("Missing block data for %s during rollback", current_entry.block_hash)
                return False
            if not self.disconnect(block):
                return False
            current_entry = self.block_index.get_block(current_entry.header.prev_block_hash.hex())
        if not current_entry or current_entry.block_hash != entry.block_hash:
            logger.error("Target rollback hash %s is not on the active chain", block_hash)
            return False
        self.block_index.set_best_chain_tip(entry.block_hash)
        return True

    def _get_block(self, block_hash: str) -> Optional[Block]:
        if callable(self._get_block_func):
            return self._get_block_func(block_hash)
        return None
