"""Transaction relay logic."""

from collections import deque
from typing import Set, Dict, Optional, Deque
from shared.core.transaction import Transaction
from shared.protocol.messages import InvMessage
from shared.utils.logging import get_logger
from node.mempool.pool import Mempool
from .peer import Peer
from .peer_scoring import PeerScoringManager

logger = get_logger()

class TransactionRelay:
    def __init__(self, mempool: Mempool, peer_scores: Optional[PeerScoringManager] = None):
        self.mempool = mempool
        self.peer_scores = peer_scores
        self.relayed: Set[str] = set()
        self.pending_inv: Dict[str, Set[Peer]] = {}
        self._pending_order: Deque[str] = deque()
        self.max_inv_per_message = 1000
        self.max_pending_inv = 5000

    async def broadcast_transaction(self, tx: Transaction, source_peer: Optional[Peer] = None) -> None:
        txid = tx.txid().hex()
        if await self.mempool.get_transaction(txid):
            return
        src = source_peer.address if source_peer else None
        if not await self.mempool.add_transaction(tx, source_peer=src):
            logger.debug(f"Transaction {txid[:16]} rejected by mempool")
            return

    async def process_inv(self, peer: Peer, inv: InvMessage) -> None:
        inventory = inv.inventory
        if len(inventory) > self.max_inv_per_message:
            if self.peer_scores:
                self.peer_scores.record_bad(peer.address, "relay_spam")
            inventory = inventory[: self.max_inv_per_message]

        seen_txids: Set[str] = set()
        for inv_type, inv_hash in inventory:
            if inv_type == InvMessage.InvType.MSG_TX:
                txid = inv_hash.hex()
                if txid in seen_txids:
                    continue
                seen_txids.add(txid)
                if await self.mempool.get_transaction(txid):
                    continue
                if txid not in self.pending_inv:
                    self.pending_inv[txid] = set()
                    self._pending_order.append(txid)
                self.pending_inv[txid].add(peer)
                await peer.send_getdata(inv_type, inv_hash)
        self._trim_pending_inv()

    async def process_transaction(self, peer: Peer, tx_data: bytes) -> None:
        tx, _ = Transaction.deserialize(tx_data)
        txid = tx.txid().hex()
        if txid in self.pending_inv:
            del self.pending_inv[txid]
        await self.broadcast_transaction(tx, peer)

    def already_relayed(self, txid: str) -> bool:
        return txid in self.relayed

    def mark_relayed(self, txid: str) -> None:
        self.relayed.add(txid)
        if len(self.relayed) > 10000:
            self.relayed.clear()

    def get_pending_count(self) -> int:
        return len(self.pending_inv)

    def _trim_pending_inv(self) -> None:
        while len(self.pending_inv) > self.max_pending_inv and self._pending_order:
            oldest = self._pending_order.popleft()
            self.pending_inv.pop(oldest, None)
