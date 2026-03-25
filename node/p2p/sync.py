"""Block synchronization logic."""

import asyncio
from typing import List, Optional, Set, Tuple, Dict
from shared.core.block import Block, BlockHeader
from shared.protocol.messages import *
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from .peer import Peer

logger = get_logger()

class BlockSync:
    def __init__(self, chainstate: ChainState):
        self.chainstate = chainstate
        self.syncing = False
        self.sync_peers: Set[Peer] = set()
        self.pending_blocks: Dict[str, asyncio.Future] = {}
        self.download_queue: List[str] = []

    async def sync_from_peer(self, peer: Peer) -> bool:
        if self.syncing:
            logger.warning("Already syncing")
            return False
        self.syncing = True
        self.sync_peers.add(peer)
        try:
            local_height = self.chainstate.get_best_height()
            remote_height = peer.peer_height
            if remote_height <= local_height:
                logger.info(f"Peer {peer.address} not ahead: {remote_height} <= {local_height}")
                return True
            logger.info(f"Syncing from {peer.address}: {local_height} -> {remote_height}")
            locator = await self._build_locator()
            await peer.send_getheaders(locator)
            return True
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return False
        finally:
            self.syncing = False
            self.sync_peers.discard(peer)

    async def _build_locator(self) -> List[bytes]:
        locator = []
        best_height = self.chainstate.get_best_height()
        step = 1
        height = best_height
        while height >= 0 and len(locator) < 100:
            block = self.chainstate.get_block_by_height(height)
            if block:
                locator.append(block.header.hash())
            if len(locator) > 10:
                step *= 2
            height -= step
        genesis = self.chainstate.get_block_by_height(0)
        if genesis:
            locator.append(genesis.header.hash())
        return locator

    async def process_headers(self, peer: Peer, headers: List[bytes]) -> None:
        if not headers:
            return
        parsed_headers = []
        for header_data in headers:
            header, _ = BlockHeader.deserialize(header_data)
            parsed_headers.append(header)
        for i, header in enumerate(parsed_headers):
            height = self.chainstate.get_best_height() + 1 + i
            if not self.chainstate.rules.validate_block_header(header, self.chainstate.get_header(height - 1)):
                logger.error(f"Invalid header at height {height}")
                return
            chainwork = self.chainstate.chainwork.calculate_block_work_from_header(header)
            self.chainstate.header_chain.add_header(header, height, chainwork)
        if len(headers) == 2000:
            await self._request_more_headers(peer)
        else:
            await self._request_blocks(peer)

    async def _request_more_headers(self, peer: Peer) -> None:
        locator = await self._build_locator()
        await peer.send_getheaders(locator)

    async def _request_blocks(self, peer: Peer) -> None:
        best_height = self.chainstate.get_best_height()
        target_height = peer.peer_height
        for height in range(best_height + 1, target_height + 1):
            hdr = self.chainstate.header_chain.get_header(height)
            if hdr:
                await peer.send_getdata(InvMessage.InvType.MSG_BLOCK, hdr.hash())

    async def process_block(self, peer: Peer, block_data: bytes) -> bool:
        block, _ = Block.deserialize(block_data)
        block_hash = block.header.hash_hex()
        height = self.chainstate.get_height(block_hash)
        if height is not None:
            return True
        expected_height = self.chainstate.get_best_height() + 1
        prev_hash = block.header.prev_block_hash.hex()
        if self.chainstate.get_best_block_hash() == prev_hash:
            if self.chainstate.rules.validate_block(block, expected_height):
                from node.validation.connect import ConnectBlock
                connect = ConnectBlock(
                    self.chainstate.utxo_store,
                    self.chainstate.block_index,
                    network=self.chainstate.params.get_network_name(),
                )
                if connect.connect(block):
                    self.chainstate.set_best_block(block_hash, expected_height, self.chainstate.chainwork.calculate_chain_work([block.header]))
                    logger.info(f"Connected block {expected_height}: {block_hash[:16]}")
                    return True
            logger.error(f"Invalid block at height {expected_height}")
            return False
        logger.debug(f"Orphan block: {block_hash[:16]}, waiting for parent")
        return True

    def is_synced(self) -> bool:
        best_height = self.chainstate.get_best_height()
        best_peer = max([p.peer_height for p in self.sync_peers], default=best_height)
        return best_height >= best_peer - 1
