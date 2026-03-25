"""Blockchain reindexing."""

from typing import Optional

from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.storage.blocks_store import BlocksStore
from node.storage.utxo_store import UTXOStore

logger = get_logger()


class Reindexer:
    """Blockchain reindexing utility."""

    def __init__(
        self,
        chainstate: ChainState,
        blocks_store: BlocksStore,
        utxo_store: UTXOStore,
    ):
        self.chainstate = chainstate
        self.blocks_store = blocks_store
        self.utxo_store = utxo_store
        self.is_reindexing = False

    async def run(
        self, start_height: int = 0, end_height: Optional[int] = None
    ) -> bool:
        self.is_reindexing = True
        try:
            logger.info("Clearing UTXO set for reindex (in-process)...")
            self.utxo_store.db.execute("DELETE FROM utxo")

            if end_height is None:
                end_height = self.chainstate.get_best_height()

            logger.info("Reindexing blocks %s to %s", start_height, end_height)

            for height in range(start_height, end_height + 1):
                await self._reindex_block(height)
                if height % 1000 == 0:
                    logger.info("Reindexed %s blocks", height)

            logger.info("Reindexing completed at height %s", end_height)
            return True
        except Exception as e:
            logger.error("Reindexing failed: %s", e)
            return False
        finally:
            self.is_reindexing = False

    async def _reindex_block(self, height: int) -> None:
        block = self.blocks_store.read_block(height)
        if not block:
            logger.warning("Block %s not found", height)
            return

        for tx in block.transactions:
            if not tx.is_coinbase():
                for txin in tx.vin:
                    self.utxo_store.spend_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)

            for i, txout in enumerate(tx.vout):
                if txout.script_pubkey and txout.script_pubkey[0] == 0x6A:
                    continue
                self.utxo_store.add_utxo(
                    txid=tx.txid().hex(),
                    index=i,
                    value=txout.value,
                    script_pubkey=txout.script_pubkey,
                    height=height,
                    is_coinbase=tx.is_coinbase(),
                )

    async def reindex_from_scratch(self) -> bool:
        logger.info("Starting full reindex from genesis...")
        return await self.run(0)

    async def reindex_last_blocks(self, count: int = 1000) -> bool:
        best_height = self.chainstate.get_best_height()
        start_height = max(0, best_height - count + 1)
        logger.info("Reindexing last %s blocks...", count)
        return await self.run(start_height, best_height)

    def get_status(self) -> dict:
        return {
            "reindexing": self.is_reindexing,
            "height": self.chainstate.get_best_height(),
        }
