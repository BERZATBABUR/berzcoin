"""Initial blockchain bootstrap and sync."""

import asyncio
import time
from typing import Any, Dict, Optional

from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.p2p.connman import ConnectionManager
from node.p2p.sync import BlockSync

logger = get_logger()


class Bootstrap:
    """Initial blockchain bootstrap."""

    def __init__(self, chainstate: ChainState, connman: ConnectionManager):
        self.chainstate = chainstate
        self.connman = connman
        self.sync = BlockSync(chainstate)
        self.is_bootstrapping = False

    async def run(self) -> bool:
        self.is_bootstrapping = True
        try:
            if self.chainstate.get_best_height() >= 0:
                logger.info("Chain already has blocks, checking sync...")

            await self._wait_for_peers()

            best_peer = self.connman.get_best_height_peer()
            if best_peer:
                logger.info(
                    "Starting bootstrap from peer at height %s",
                    best_peer.peer_height,
                )
                await self.sync.sync_from_peer(best_peer)

            await self._wait_for_sync()

            logger.info(
                "Bootstrap completed at height %s",
                self.chainstate.get_best_height(),
            )
            return True
        except Exception as e:
            logger.error("Bootstrap failed: %s", e)
            return False
        finally:
            self.is_bootstrapping = False

    async def _wait_for_peers(self, timeout: int = 60) -> None:
        start = time.monotonic()
        while self.connman.get_connected_count() == 0:
            if time.monotonic() - start > timeout:
                logger.warning("Timeout waiting for peers")
                return
            logger.info("Waiting for peers...")
            await asyncio.sleep(5)

    async def _wait_for_sync(self) -> None:
        while not self.sync.is_synced():
            best_height = self.chainstate.get_best_height()
            best_peer = self.connman.get_best_height_peer()
            if best_peer:
                logger.info("Syncing: %s / %s", best_height, best_peer.peer_height)
            await asyncio.sleep(10)

    async def resync(self) -> bool:
        logger.info("Starting resync...")
        return await self.run()

    def get_status(self) -> Dict[str, Any]:
        best_height = self.chainstate.get_best_height()
        best_peer: Optional[Any] = self.connman.get_best_height_peer()
        target = best_peer.peer_height if best_peer else best_height
        progress = (
            best_height / best_peer.peer_height
            if best_peer and best_peer.peer_height > 0
            else 1.0
        )
        return {
            "bootstrapping": self.is_bootstrapping,
            "height": best_height,
            "target_height": target,
            "progress": progress,
            "peers": self.connman.get_connected_count(),
        }
