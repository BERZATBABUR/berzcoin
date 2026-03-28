"""Block synchronization logic."""

import asyncio
from typing import List, Optional, Set, Tuple, Dict
from shared.core.block import Block, BlockHeader
from shared.protocol.messages import *
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool
from .orphanage import Orphanage
from .peer import Peer

logger = get_logger()

class BlockSync:
    def __init__(
        self,
        chainstate: ChainState,
        mempool: Optional[Mempool] = None,
        block_handler=None,
        getdata_batch_size: int = 128,
        block_request_timeout_secs: int = 30,
        max_orphans: int = 200,
        max_orphan_age_secs: int = 7200,
    ):
        self.chainstate = chainstate
        self.mempool = mempool
        self.block_handler = block_handler
        self.syncing = False
        self.sync_peers: Set[Peer] = set()
        self.pending_blocks: Dict[str, asyncio.Future] = {}
        self.download_queue: List[str] = []
        self.orphanage = Orphanage(
            max_orphans=max(1, int(max_orphans)),
            max_age=max(60, int(max_orphan_age_secs)),
        )
        self.getdata_batch_size = max(1, int(getdata_batch_size))
        self.blocks_requested = 0
        self.getdata_messages_sent = 0
        self._pending_block_requests: Dict[str, float] = {}
        self._block_request_timeout_secs = max(5, int(block_request_timeout_secs))

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
        inventory: List[Tuple[int, bytes]] = []
        for height in range(best_height + 1, target_height + 1):
            if hasattr(self.chainstate, "get_header"):
                hdr = self.chainstate.get_header(height)
            else:
                hdr = self.chainstate.header_chain.get_header(height)
            if not hdr:
                continue
            inventory.append((InvMessage.InvType.MSG_BLOCK, hdr.hash()))
            if len(inventory) >= self.getdata_batch_size:
                await self._send_getdata_batch(peer, inventory)
                inventory = []
        if inventory:
            await self._send_getdata_batch(peer, inventory)

    async def _send_getdata_batch(self, peer: Peer, inventory: List[Tuple[int, bytes]]) -> None:
        if not inventory:
            return
        msg = GetDataMessage(inventory=list(inventory))
        await peer.send_message("getdata", msg.serialize())
        self.getdata_messages_sent += 1
        self.blocks_requested += len(inventory)
        now = asyncio.get_event_loop().time()
        for _inv_type, inv_hash in inventory:
            self._pending_block_requests[inv_hash.hex()] = now

    async def process_block(self, peer: Peer, block_data: bytes) -> bool:
        block, _ = Block.deserialize(block_data)
        block_hash = block.header.hash_hex()
        self._pending_block_requests.pop(block_hash, None)
        self._cleanup_stale_requests()
        self.orphanage.cleanup_expired()
        if self.block_handler is not None:
            accepted, _bh, reason = await self.block_handler(
                block,
                peer.address if peer else None,
                False,
            )
            if accepted or reason in ("known", "stored_fork"):
                return True
            if reason == "orphan":
                self.orphanage.add_orphan(block, source_peer=peer.address if peer else None)
                return True
            logger.error("Rejected block %s: %s", block_hash[:16], reason)
            return False

        height = self.chainstate.get_height(block_hash)
        if height is not None:
            return True
        expected_height = self.chainstate.get_best_height() + 1
        prev_hash = block.header.prev_block_hash.hex()
        if self.chainstate.get_best_block_hash() == prev_hash:
            if self.chainstate.validate_block_stateful(block, expected_height):
                from node.validation.connect import ConnectBlock
                block_work = self.chainstate.chainwork.calculate_chain_work([block.header])
                chainwork_total = self.chainstate.get_best_chainwork() + block_work
                self.chainstate.blocks_store.write_block(block, expected_height)
                self.chainstate.block_index.add_block(block, expected_height, chainwork_total)
                connect = ConnectBlock(
                    self.chainstate.utxo_store,
                    self.chainstate.block_index,
                    network=self.chainstate.params.get_network_name(),
                )
                if connect.connect(block):
                    self.chainstate.set_best_block(block_hash, expected_height, chainwork_total)
                    self.chainstate.header_chain.add_header(block.header, expected_height, chainwork_total)
                    if self.mempool is not None:
                        await self.mempool.handle_connected_block(block)
                    logger.info(f"Connected block {expected_height}: {block_hash[:16]}")
                    await self._process_orphan_children(peer, block_hash)
                    return True
            logger.error(f"Invalid block at height {expected_height}")
            return False
        self.orphanage.add_orphan(block, source_peer=peer.address if peer else None)
        logger.debug(f"Orphan block: {block_hash[:16]}, waiting for parent {prev_hash[:16]}")
        return True

    async def _process_orphan_children(self, peer: Peer, parent_hash: str) -> None:
        """Try to connect orphan descendants once a parent is connected."""
        children = self.orphanage.get_children(parent_hash)
        for child in children:
            child_hash = child.header.hash_hex()
            self.orphanage.remove_orphan(child_hash)
            await self.process_block(peer, child.serialize())

    def is_synced(self) -> bool:
        best_height = self.chainstate.get_best_height()
        best_peer = max([p.peer_height for p in self.sync_peers], default=best_height)
        return best_height >= best_peer - 1

    def get_stats(self) -> Dict[str, int]:
        return {
            "blocks_requested": self.blocks_requested,
            "getdata_messages_sent": self.getdata_messages_sent,
            "pending_blocks": len(self.pending_blocks),
            "download_queue": len(self.download_queue),
            "pending_block_requests": len(self._pending_block_requests),
        }

    def _cleanup_stale_requests(self) -> int:
        now = asyncio.get_event_loop().time()
        stale = [h for h, ts in self._pending_block_requests.items() if now - ts > self._block_request_timeout_secs]
        for h in stale:
            self._pending_block_requests.pop(h, None)
        return len(stale)
