"""Transaction pool management."""

import time
import asyncio
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from shared.core.transaction import Transaction
from shared.core.hashes import hash256
from shared.consensus.weights import calculate_transaction_weight
from shared.protocol.messages import InvMessage
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from ..p2p.connman import ConnectionManager
from .policy import MempoolPolicy
from .limits import MempoolLimits
from .fees import FeeCalculator

logger = get_logger()

@dataclass
class MempoolEntry:
    tx: Transaction
    txid: str
    size: int
    weight: int
    fee: int
    fee_rate: float
    time_added: float
    height_added: int
    ancestors: Set[str] = field(default_factory=set)
    descendants: Set[str] = field(default_factory=set)

    @property
    def age(self) -> float:
        return time.time() - self.time_added

class Mempool:
    def __init__(self, chainstate: ChainState, policy: MempoolPolicy = None,
                 limits: MempoolLimits = None, connman: Optional[ConnectionManager] = None):
        self.chainstate = chainstate
        self.policy = policy or MempoolPolicy()
        self.limits = limits or MempoolLimits()
        self.connman = connman
        self.fee_calculator = FeeCalculator()
        self.transactions: Dict[str, MempoolEntry] = {}
        self.tx_by_fee: List[str] = []
        self.tx_by_time: List[str] = []
        self.spent_utxos: Dict[str, Set[int]] = {}
        self.unconfirmed_parents: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self.total_size = 0
        self.total_weight = 0
        self.total_fee = 0
        # Last rejection reason for the most recent failed add_transaction attempt
        self.last_reject_reason: Optional[str] = None

    async def add_transaction(self, tx: Transaction, source_peer: Optional[str] = None) -> bool:
        async with self._lock:
            txid = tx.txid().hex()
            if txid in self.transactions:
                logger.debug(f"Transaction {txid[:16]} already in mempool")
                self.last_reject_reason = "already_in_mempool"
                return False
            if not await self._validate_transaction(tx):
                # _validate_transaction logs specifics; set a generic reason
                self.last_reject_reason = "validation_failed"
                return False
            if not self.policy.is_standard(tx):
                logger.debug(f"Transaction {txid[:16]} not standard")
                self.last_reject_reason = "non_standard"
                return False
            fee = await self._calculate_fee(tx)
            if fee < self.policy.min_relay_fee * tx.size():
                logger.debug(f"Transaction {txid[:16]} fee too low")
                self.last_reject_reason = "fee_too_low"
                return False
            parents = await self._check_dependencies(tx)
            if parents is None:
                logger.debug(f"Transaction {txid[:16]} has missing parents")
                self.last_reject_reason = "missing_parents"
                return False
            size = len(tx.serialize())
            weight = calculate_transaction_weight(tx)
            if not self.limits.can_accept(size, weight, len(self.transactions)):
                logger.debug("Mempool limits reached")
                self.last_reject_reason = "mempool_limits"
                return False
            entry = MempoolEntry(
                tx=tx,
                txid=txid,
                size=size,
                weight=weight,
                fee=fee,
                fee_rate=fee / size,
                time_added=time.time(),
                height_added=self.chainstate.get_best_height(),
                ancestors=set(),
                descendants=set(),
            )
            self.transactions[txid] = entry
            self.total_size += size
            self.total_weight += weight
            self.total_fee += fee
            self._update_indexes(txid, entry)
            # Record fee for estimation/history
            try:
                self.fee_calculator.add_transaction(entry.fee, entry.size, entry.height_added)
            except Exception:
                logger.debug("FeeCalculator.add_transaction failed")
            self._track_spent_utxos(tx, txid)
            if parents:
                self.unconfirmed_parents[txid] = parents
                for parent in parents:
                    if parent in self.transactions:
                        self.transactions[parent].descendants.add(txid)
                        entry.ancestors.add(parent)
            logger.info(f"Added transaction {txid[:16]} to mempool (fee: {fee} sat, size: {size})")
        await self._broadcast_tx_inv(tx, source_peer)
        # Clear last_reject_reason on success
        self.last_reject_reason = None
        return True

    async def _broadcast_tx_inv(self, tx: Transaction, source_peer: Optional[str]) -> None:
        if not self.connman:
            return
        txid_hex = tx.txid().hex()
        inv = InvMessage(inventory=[(InvMessage.InvType.MSG_TX, tx.txid())])
        try:
            await self.connman.broadcast(
                "inv",
                inv.serialize(),
                exclude={source_peer} if source_peer else None,
            )
            logger.debug(f"Broadcast transaction {txid_hex[:16]} to peers")
        except Exception as e:
            logger.debug(f"Broadcast inv failed for {txid_hex[:16]}: {e}")

    async def remove_transaction(self, txid: str, include_descendants: bool = True) -> List[str]:
        async with self._lock:
            removed = []
            if txid not in self.transactions:
                return removed
            to_remove = {txid}
            if include_descendants:
                to_remove.update(self._get_descendants(txid))
            for txid_remove in to_remove:
                entry = self.transactions.get(txid_remove)
                if not entry:
                    continue
                del self.transactions[txid_remove]
                self.total_size -= entry.size
                self.total_weight -= entry.weight
                self.total_fee -= entry.fee
                removed.append(txid_remove)
                self._remove_from_indexes(txid_remove)
                self._untrack_spent_utxos(entry.tx, txid_remove)
                self.unconfirmed_parents.pop(txid_remove, None)
                for ancestor in entry.ancestors:
                    if ancestor in self.transactions:
                        self.transactions[ancestor].descendants.discard(txid_remove)
                for descendant in entry.descendants:
                    if descendant in self.transactions:
                        self.transactions[descendant].ancestors.discard(txid_remove)
            logger.info(f"Removed {len(removed)} transactions from mempool")
            return removed

    async def get_transaction(self, txid: str) -> Optional[Transaction]:
        entry = self.transactions.get(txid)
        return entry.tx if entry else None

    async def get_transactions(self, limit: int = None) -> List[Transaction]:
        txids = self.tx_by_fee[:limit] if limit else self.tx_by_fee
        return [self.transactions[txid].tx for txid in txids if txid in self.transactions]

    async def get_transactions_for_block(self, max_weight: int) -> List[Transaction]:
        selected = []
        current_weight = 0
        for txid in self.tx_by_fee:
            entry = self.transactions.get(txid)
            if not entry:
                continue
            if not await self._are_ancestors_selected(txid, selected):
                continue
            if current_weight + entry.weight <= max_weight:
                selected.append(entry.tx)
                current_weight += entry.weight
        return selected

    async def get_ancestors(self, txid: str) -> List[Transaction]:
        entry = self.transactions.get(txid)
        if not entry:
            return []
        return [self.transactions[a].tx for a in entry.ancestors if a in self.transactions]

    async def get_descendants(self, txid: str) -> List[Transaction]:
        entry = self.transactions.get(txid)
        if not entry:
            return []
        return [self.transactions[d].tx for d in entry.descendants if d in self.transactions]

    async def _validate_transaction(self, tx: Transaction) -> bool:
        if self.chainstate.transaction_exists(tx.txid().hex()):
            logger.debug("Transaction already in blockchain")
            return False
        for txin in tx.vin:
            utxo = self.chainstate.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
            if not utxo:
                logger.debug(f"UTXO not found: {txin.prev_tx_hash.hex()}:{txin.prev_tx_index}")
                return False
            if txin.prev_tx_hash.hex() in self.spent_utxos and txin.prev_tx_index in self.spent_utxos[txin.prev_tx_hash.hex()]:
                logger.debug("UTXO already spent in mempool")
                return False
        return True

    async def _calculate_fee(self, tx: Transaction) -> int:
        total_input = 0
        total_output = sum(txout.value for txout in tx.vout)
        for txin in tx.vin:
            utxo = self.chainstate.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
            if utxo:
                total_input += utxo['value']
        return total_input - total_output

    async def _check_dependencies(self, tx: Transaction) -> Optional[Set[str]]:
        parents = set()
        for txin in tx.vin:
            parent_txid = txin.prev_tx_hash.hex()
            if self.chainstate.transaction_exists(parent_txid):
                continue
            if parent_txid not in self.transactions:
                logger.debug(f"Missing parent: {parent_txid}")
                return None
            parents.add(parent_txid)
        return parents if parents else None

    async def _are_ancestors_selected(self, txid: str, selected: List[Transaction]) -> bool:
        entry = self.transactions.get(txid)
        if not entry:
            return True
        selected_txids = {tx.txid().hex() for tx in selected}
        return all(a in selected_txids for a in entry.ancestors)

    def _update_indexes(self, txid: str, entry: MempoolEntry) -> None:
        self.tx_by_fee.append(txid)
        self.tx_by_fee.sort(key=lambda x: self.transactions[x].fee_rate, reverse=True)
        self.tx_by_time.append(txid)
        self.tx_by_time.sort(key=lambda x: self.transactions[x].time_added)

    def _remove_from_indexes(self, txid: str) -> None:
        if txid in self.tx_by_fee:
            self.tx_by_fee.remove(txid)
        if txid in self.tx_by_time:
            self.tx_by_time.remove(txid)

    def _track_spent_utxos(self, tx: Transaction, txid: str) -> None:
        for txin in tx.vin:
            parent_txid = txin.prev_tx_hash.hex()
            parent_index = txin.prev_tx_index
            if parent_txid not in self.spent_utxos:
                self.spent_utxos[parent_txid] = set()
            self.spent_utxos[parent_txid].add(parent_index)

    def _untrack_spent_utxos(self, tx: Transaction, txid: str) -> None:
        for txin in tx.vin:
            parent_txid = txin.prev_tx_hash.hex()
            parent_index = txin.prev_tx_index
            if parent_txid in self.spent_utxos:
                self.spent_utxos[parent_txid].discard(parent_index)
                if not self.spent_utxos[parent_txid]:
                    del self.spent_utxos[parent_txid]

    def _get_descendants(self, txid: str) -> Set[str]:
        descendants = set()
        queue = [txid]
        while queue:
            current = queue.pop(0)
            entry = self.transactions.get(current)
            if entry:
                for descendant in entry.descendants:
                    if descendant not in descendants:
                        descendants.add(descendant)
                        queue.append(descendant)
        return descendants

    async def get_stats(self) -> Dict:
        min_fee_rate = self.transactions[self.tx_by_fee[-1]].fee_rate if self.tx_by_fee else 0
        max_fee_rate = self.transactions[self.tx_by_fee[0]].fee_rate if self.tx_by_fee else 0
        return {
            'size': len(self.transactions),
            'total_size': self.total_size,
            'total_weight': self.total_weight,
            'total_fee': self.total_fee,
            'min_fee_rate': min_fee_rate,
            'max_fee_rate': max_fee_rate,
            'avg_fee_rate': self.total_fee / self.total_size if self.total_size else 0,
            'limits': self.limits.get_stats(),
        }
