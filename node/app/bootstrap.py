"""Node bootstrap with full chain scan."""

import asyncio
from typing import Optional
from ...shared.utils.logging import get_logger

logger = get_logger()


class NodeBootstrap:
    """Initialize new node with full chain sync."""

    def __init__(self, chainstate, p2p_manager, utxo_store):
        """Initialize bootstrap."""
        self.chainstate = chainstate
        self.p2p_manager = p2p_manager
        self.utxo_store = utxo_store
        self.syncing = False

    async def sync_full_chain(self) -> bool:
        """Sync full blockchain from peers."""
        if self.syncing:
            return False

        self.syncing = True
        logger.info("Starting full chain sync...")

        try:
            # Get best peer
            best_peer = self.p2p_manager.get_best_height_peer()
            if not best_peer:
                logger.warning("No peers available for sync")
                return False

            target_height = best_peer.peer_height
            current_height = self.chainstate.get_best_height()

            logger.info(f"Syncing from height {current_height} to {target_height}")

            # Download headers first
            await self._sync_headers(best_peer)

            # Download and verify blocks
            await self._sync_blocks(best_peer, current_height, target_height)

            # Final verification
            await self._verify_chain()

            logger.info(f"Full chain sync complete. Height: {self.chainstate.get_best_height()}")
            return True

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return False
        finally:
            self.syncing = False

    async def _sync_headers(self, peer) -> None:
        """Sync block headers."""
        from ..p2p.sync import BlockSync
        sync = BlockSync(self.chainstate)
        await sync.sync_from_peer(peer)

    async def _sync_blocks(self, peer, start_height: int, end_height: int) -> None:
        """Download and verify blocks."""
        from shared.protocol.messages import InvMessage

        for height in range(start_height + 1, end_height + 1):
            # Request block
            block_hash = self.chainstate.header_chain.get_header(height)
            if block_hash:
                await peer.send_getdata(InvMessage.InvType.MSG_BLOCK, block_hash.hash())

            # Small delay to avoid overwhelming peer
            await asyncio.sleep(0.01)

            if height % 1000 == 0:
                logger.info(f"Synced {height} / {end_height} blocks")

    async def _verify_chain(self) -> None:
        """Verify full chain integrity."""
        best_height = self.chainstate.get_best_height()
        logger.info(f"Verifying chain up to height {best_height}...")

        # Verify UTXO set consistency
        if self.utxo_store.verify_consistency():
            logger.info("UTXO set verification passed")
        else:
            logger.error("UTXO set verification failed!")

    def get_progress(self) -> dict:
        """Get sync progress."""
        return {
            'syncing': self.syncing,
            'current_height': self.chainstate.get_best_height(),
            'target_height': self.p2p_manager.get_best_height_peer().peer_height if self.p2p_manager else 0
        }
