"""Transaction pool management."""

import time
import asyncio
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from shared.core.transaction import Transaction
from shared.consensus.weights import calculate_transaction_weight
from shared.protocol.messages import InvMessage
from shared.script.verify import verify_input_script
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
    vsize: int
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
        self.total_vsize = 0
        self.total_weight = 0
        self.total_fee = 0
        # Last rejection reason for the most recent failed add_transaction attempt
        self.last_reject_reason: Optional[str] = None

    @staticmethod
    def _virtual_size_from_weight(weight: int) -> int:
        # Bitcoin-style virtual size rounding: ceil(weight / 4).
        return max(1, (int(weight) + 3) // 4)

    async def add_package(
        self,
        txs: List[Transaction],
        source_peer: Optional[str] = None,
    ) -> Dict[str, object]:
        """Add a package of dependent transactions atomically.

        Transactions are topologically sorted by in-package dependencies.
        On the first rejection, all newly-added package members are rolled back.
        """
        ordered = self._sort_package_txs(txs)
        if ordered is None:
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_topology_invalid",
            }
        if len(ordered) > self.limits.max_package_count:
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_too_many_transactions",
            }
        package_weight = sum(calculate_transaction_weight(tx) for tx in ordered)
        if package_weight > self.limits.max_package_weight:
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_too_heavy",
            }

        added: List[str] = []
        for tx in ordered:
            ok = await self.add_transaction(tx, source_peer=source_peer)
            if not ok:
                for txid in reversed(added):
                    await self.remove_transaction(txid, include_descendants=True)
                return {
                    "accepted": False,
                    "added": [],
                    "reject-reason": self.last_reject_reason or "package_rejected",
                    "failed-txid": tx.txid().hex(),
                }
            added.append(tx.txid().hex())

        return {
            "accepted": True,
            "added": added,
            "count": len(added),
        }

    async def add_transaction(self, tx: Transaction, source_peer: Optional[str] = None) -> bool:
        async with self._lock:
            txid = tx.txid().hex()
            if txid in self.transactions:
                logger.debug(f"Transaction {txid[:16]} already in mempool")
                self.last_reject_reason = "already_in_mempool"
                return False
            fee = await self._calculate_fee(tx)
            size = len(tx.serialize())
            weight = calculate_transaction_weight(tx)
            vsize = self._virtual_size_from_weight(weight)
            fee_rate = fee / vsize if vsize > 0 else 0
            conflicting_txids = self._find_conflicting_txids(tx)
            ignored_spent = self._build_ignored_spent_set(conflicting_txids)

            if conflicting_txids and not self._can_replace(conflicting_txids, tx, fee, vsize, fee_rate):
                logger.debug("Replacement policy rejected transaction %s", txid[:16])
                self.last_reject_reason = "rbf_policy"
                return False

            if not await self._validate_transaction(tx, ignored_spent_outpoints=ignored_spent):
                # _validate_transaction logs specifics and may set a reject reason.
                self.last_reject_reason = self.last_reject_reason or "validation_failed"
                return False
            if not self.policy.is_standard(tx):
                logger.debug(f"Transaction {txid[:16]} not standard")
                self.last_reject_reason = "non_standard"
                return False
            if fee < self.policy.get_min_fee_for_vsize(vsize):
                logger.debug(f"Transaction {txid[:16]} fee too low")
                self.last_reject_reason = "fee_too_low"
                return False
            parents = await self._check_dependencies(tx)
            if parents is None:
                logger.debug(f"Transaction {txid[:16]} has missing parents")
                self.last_reject_reason = "missing_parents"
                return False
            ancestors = self._collect_ancestors(parents)
            if len(ancestors) > self.limits.max_ancestors:
                logger.debug("Too many ancestors")
                self.last_reject_reason = "too_many_ancestors"
                return False
            ancestor_package_vsize = vsize + sum(
                self.transactions[a].vsize for a in ancestors if a in self.transactions
            )
            if ancestor_package_vsize > self.limits.max_ancestor_size_vbytes:
                logger.debug("Ancestor package too large")
                self.last_reject_reason = "ancestor_package_too_large"
                return False
            if not self.limits.can_accept(
                size, weight, len(self.transactions), self.total_size, self.total_weight
            ):
                # Try fee-rate-based eviction before rejecting.
                await self._evict_for_space(size, weight, fee_rate)
            if not self.limits.can_accept(
                size, weight, len(self.transactions), self.total_size, self.total_weight
            ):
                logger.debug("Mempool limits reached")
                self.last_reject_reason = "mempool_limits"
                return False

            if conflicting_txids:
                replacement_set = set(conflicting_txids)
                for conflict_txid in list(conflicting_txids):
                    replacement_set.update(self._get_descendants(conflict_txid))
                for conflict_txid in list(replacement_set):
                    if conflict_txid in self.transactions:
                        self._remove_transaction_nolock(conflict_txid, include_descendants=True)

            entry = MempoolEntry(
                tx=tx,
                txid=txid,
                size=size,
                vsize=vsize,
                weight=weight,
                fee=fee,
                fee_rate=fee_rate,
                time_added=time.time(),
                height_added=self.chainstate.get_best_height(),
                ancestors=set(),
                descendants=set(),
            )
            self.transactions[txid] = entry
            self.total_size += size
            self.total_vsize += vsize
            self.total_weight += weight
            self.total_fee += fee
            self._update_indexes(txid, entry)
            # Record fee for estimation/history
            try:
                self.fee_calculator.add_transaction(entry.fee, entry.vsize, entry.height_added)
            except Exception:
                logger.debug("FeeCalculator.add_transaction failed")
            self._track_spent_utxos(tx, txid)
            if ancestors:
                self.unconfirmed_parents[txid] = set(parents)
                entry.ancestors = set(ancestors)
                for ancestor in ancestors:
                    if ancestor in self.transactions:
                        self.transactions[ancestor].descendants.add(txid)
                        if len(self.transactions[ancestor].descendants) > self.limits.max_descendants:
                            self.last_reject_reason = "too_many_descendants"
                            # Undo local insertion path via remove_transaction.
                            self._remove_transaction_nolock(txid, include_descendants=False)
                            return False
                        descendant_package_vsize = (
                            self.transactions[ancestor].vsize
                            + sum(
                                self.transactions[d].vsize
                                for d in self.transactions[ancestor].descendants
                                if d in self.transactions
                            )
                        )
                        if descendant_package_vsize > self.limits.max_descendant_size_vbytes:
                            self.last_reject_reason = "descendant_package_too_large"
                            self._remove_transaction_nolock(txid, include_descendants=False)
                            return False
            logger.info(f"Added transaction {txid[:16]} to mempool (fee: {fee} sat, size: {size})")
        await self._broadcast_tx_inv(tx, source_peer)
        # Clear last_reject_reason on success
        self.last_reject_reason = None
        return True

    async def _evict_for_space(self, need_size: int, need_weight: int, incoming_fee_rate: float) -> None:
        """Evict low-fee transactions until limits can accept incoming tx."""
        if not self.transactions:
            return
        # Lowest fee-rate first.
        txids = sorted(self.transactions.keys(), key=lambda t: self.transactions[t].fee_rate)
        for txid in txids:
            if self.limits.can_accept(
                need_size, need_weight, len(self.transactions), self.total_size, self.total_weight
            ):
                break
            entry = self.transactions.get(txid)
            if not entry:
                continue
            # Do not evict if incoming doesn't outbid the candidate.
            if incoming_fee_rate <= entry.fee_rate:
                break
            await self.remove_transaction(txid, include_descendants=True)

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
            removed = self._remove_transaction_nolock(txid, include_descendants=include_descendants)
            logger.info(f"Removed {len(removed)} transactions from mempool")
            return removed

    async def get_transaction(self, txid: str) -> Optional[Transaction]:
        entry = self.transactions.get(txid)
        return entry.tx if entry else None

    async def get_transactions(self, limit: int = None) -> List[Transaction]:
        txids = self.tx_by_fee[:limit] if limit else self.tx_by_fee
        return [self.transactions[txid].tx for txid in txids if txid in self.transactions]

    async def get_transactions_for_block(self, max_weight: int) -> List[Transaction]:
        selected: List[Transaction] = []
        selected_txids: Set[str] = set()
        current_weight = 0

        while True:
            best_pkg: Optional[List[str]] = None
            best_rate = -1.0
            best_weight = 0
            for txid in self.tx_by_fee:
                if txid in selected_txids or txid not in self.transactions:
                    continue
                pkg_ids = self._get_unselected_ancestor_package(txid, selected_txids)
                if not pkg_ids:
                    continue
                pkg_weight = sum(self.transactions[t].weight for t in pkg_ids if t in self.transactions)
                if pkg_weight <= 0 or current_weight + pkg_weight > max_weight:
                    continue
                pkg_fee = sum(self.transactions[t].fee for t in pkg_ids if t in self.transactions)
                pkg_rate = pkg_fee / pkg_weight
                if pkg_rate > best_rate:
                    best_rate = pkg_rate
                    best_pkg = pkg_ids
                    best_weight = pkg_weight

            if not best_pkg:
                break

            for txid in best_pkg:
                if txid in selected_txids or txid not in self.transactions:
                    continue
                selected.append(self.transactions[txid].tx)
                selected_txids.add(txid)
            current_weight += best_weight
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

    async def handle_connected_block(self, block: object) -> List[str]:
        """Remove confirmed txs and evict entries invalidated by the new chain tip."""
        removed: List[str] = []
        async with self._lock:
            txs = list(getattr(block, "transactions", []) or [])
            confirmed_txids = {
                tx.txid().hex()
                for tx in txs
                if hasattr(tx, "txid") and not getattr(tx, "is_coinbase", lambda: False)()
            }
            for txid in confirmed_txids:
                removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))

            # Revalidate remaining transactions against updated chain UTXO set.
            for txid in list(self.transactions.keys()):
                entry = self.transactions.get(txid)
                if not entry:
                    continue
                tx = entry.tx
                own_inputs = {(txin.prev_tx_hash.hex(), int(txin.prev_tx_index)) for txin in tx.vin}
                if not await self._validate_transaction(tx, ignored_spent_outpoints=own_inputs):
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    continue
                parents = await self._check_dependencies(tx)
                if parents is None:
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    continue
                if not self.policy.is_standard(tx):
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    continue
                fee = await self._calculate_fee(tx)
                tx_vsize = self._virtual_size_from_weight(calculate_transaction_weight(tx))
                if fee < self.policy.get_min_fee_for_vsize(tx_vsize):
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    continue
        return removed

    async def _validate_transaction(
        self,
        tx: Transaction,
        ignored_spent_outpoints: Optional[Set[Tuple[str, int]]] = None,
    ) -> bool:
        self.last_reject_reason = None
        if tx.is_coinbase():
            logger.debug("Coinbase transaction not allowed in mempool")
            self.last_reject_reason = "coinbase_in_mempool"
            return False
        if not tx.vin or not tx.vout:
            logger.debug("Transaction missing inputs or outputs")
            self.last_reject_reason = "bad_tx_shape"
            return False

        params = getattr(self.chainstate, "params", None)
        max_money = int(getattr(params, "max_money", 21_000_000 * 100_000_000))
        total_out = 0
        for txout in tx.vout:
            if int(txout.value) < 0:
                logger.debug("Negative output value")
                self.last_reject_reason = "negative_output"
                return False
            total_out += int(txout.value)
            if total_out > max_money:
                logger.debug("Total output exceeds max money")
                self.last_reject_reason = "output_exceeds_max_money"
                return False

        if self.chainstate.transaction_exists(tx.txid().hex()):
            logger.debug("Transaction already in blockchain")
            self.last_reject_reason = "already_in_chain"
            return False
        ignored = ignored_spent_outpoints or set()
        seen_inputs: Set[Tuple[str, int]] = set()
        total_in = 0
        for idx, txin in enumerate(tx.vin):
            prev_txid = txin.prev_tx_hash.hex()
            prev_index = txin.prev_tx_index
            outpoint = (prev_txid, int(prev_index))
            if outpoint in seen_inputs:
                logger.debug("Duplicate input in transaction")
                return False
            seen_inputs.add(outpoint)
            utxo = self.chainstate.get_utxo(prev_txid, prev_index)
            if not utxo and prev_txid in self.transactions:
                parent_entry = self.transactions.get(prev_txid)
                if parent_entry and 0 <= int(prev_index) < len(parent_entry.tx.vout):
                    prev_out = parent_entry.tx.vout[int(prev_index)]
                    utxo = {
                        "value": prev_out.value,
                        "script_pubkey": prev_out.script_pubkey,
                        "height": self.chainstate.get_best_height(),
                        "is_coinbase": False,
                    }
            if not utxo:
                logger.debug(f"UTXO not found: {prev_txid}:{prev_index}")
                self.last_reject_reason = "missing_utxo"
                return False
            if bool(utxo.get("is_coinbase", False)):
                next_height = int(self.chainstate.get_best_height()) + 1
                utxo_height = int(utxo.get("height", 0) or 0)
                maturity = int(getattr(params, "coinbase_maturity", 100))
                if next_height - utxo_height < maturity:
                    logger.debug("Coinbase spend not mature for mempool acceptance")
                    self.last_reject_reason = "coinbase_not_mature"
                    return False
            total_in += int(utxo.get("value", 0))
            if (
                (prev_txid, prev_index) not in ignored
                and prev_txid in self.spent_utxos
                and prev_index in self.spent_utxos[prev_txid]
            ):
                logger.debug("UTXO already spent in mempool")
                self.last_reject_reason = "utxo_already_spent_in_mempool"
                return False
            script_pubkey = utxo.get("script_pubkey", b"")
            if not isinstance(script_pubkey, (bytes, bytearray)):
                script_pubkey = bytes(script_pubkey)
            if not verify_input_script(
                tx,
                idx,
                txin.script_sig,
                bytes(script_pubkey),
                int(utxo.get("value", 0)),
            ):
                logger.debug("Input script/signature validation failed")
                self.last_reject_reason = "script_verification_failed"
                return False
        if total_in < total_out:
            logger.debug("Transaction spends more than its inputs")
            self.last_reject_reason = "inputs_less_than_outputs"
            return False
        return True

    async def _calculate_fee(self, tx: Transaction) -> int:
        total_input = 0
        total_output = sum(txout.value for txout in tx.vout)
        for txin in tx.vin:
            prev_txid = txin.prev_tx_hash.hex()
            prev_index = txin.prev_tx_index
            utxo = self.chainstate.get_utxo(prev_txid, prev_index)
            if not utxo and prev_txid in self.transactions:
                parent_entry = self.transactions.get(prev_txid)
                if parent_entry and 0 <= int(prev_index) < len(parent_entry.tx.vout):
                    prev_out = parent_entry.tx.vout[int(prev_index)]
                    utxo = {"value": prev_out.value}
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
        return parents

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
        _ = txid
        for txin in tx.vin:
            parent_txid = txin.prev_tx_hash.hex()
            parent_index = txin.prev_tx_index
            if parent_txid not in self.spent_utxos:
                self.spent_utxos[parent_txid] = set()
            self.spent_utxos[parent_txid].add(parent_index)

    def _untrack_spent_utxos(self, tx: Transaction, txid: str) -> None:
        _ = txid
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

    def _collect_ancestors(self, parents: Optional[Set[str]]) -> Set[str]:
        if not parents:
            return set()
        ancestors: Set[str] = set()
        queue = list(parents)
        while queue:
            current = queue.pop(0)
            if current in ancestors:
                continue
            ancestors.add(current)
            parent_entry = self.transactions.get(current)
            if parent_entry:
                for ancestor in parent_entry.ancestors:
                    if ancestor not in ancestors:
                        queue.append(ancestor)
        return ancestors

    def _remove_transaction_nolock(self, txid: str, include_descendants: bool = True) -> List[str]:
        removed: List[str] = []
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
            self.total_vsize -= entry.vsize
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
        return removed

    def _find_conflicting_txids(self, tx: Transaction) -> Set[str]:
        conflicts: Set[str] = set()
        for entry_txid, entry in self.transactions.items():
            spent = {
                (i.prev_tx_hash.hex(), i.prev_tx_index)
                for i in entry.tx.vin
            }
            for txin in tx.vin:
                if (txin.prev_tx_hash.hex(), txin.prev_tx_index) in spent:
                    conflicts.add(entry_txid)
                    break
        return conflicts

    def _sort_package_txs(self, txs: List[Transaction]) -> Optional[List[Transaction]]:
        tx_by_id: Dict[str, Transaction] = {tx.txid().hex(): tx for tx in txs}
        unresolved = set(tx_by_id.keys())
        ordered: List[Transaction] = []

        while unresolved:
            progressed = False
            for txid in list(unresolved):
                tx = tx_by_id[txid]
                deps: Set[str] = set()
                for txin in tx.vin:
                    parent = txin.prev_tx_hash.hex()
                    if parent in tx_by_id:
                        deps.add(parent)
                if deps.issubset({t.txid().hex() for t in ordered}):
                    ordered.append(tx)
                    unresolved.remove(txid)
                    progressed = True
            if not progressed:
                return None
        return ordered

    def _topo_sort_txids(self, txids: Set[str]) -> List[str]:
        remaining = set(txids)
        ordered: List[str] = []
        while remaining:
            progressed = False
            for txid in list(remaining):
                parents = self.unconfirmed_parents.get(txid, set())
                if all((p not in txids) or (p in ordered) for p in parents):
                    ordered.append(txid)
                    remaining.remove(txid)
                    progressed = True
            if not progressed:
                ordered.extend(sorted(remaining))
                break
        return ordered

    def _get_unselected_ancestor_package(self, txid: str, selected_txids: Set[str]) -> List[str]:
        if txid not in self.transactions:
            return []
        needed = {a for a in self.transactions[txid].ancestors if a not in selected_txids and a in self.transactions}
        needed.add(txid)
        return self._topo_sort_txids(needed)

    def _build_ignored_spent_set(self, conflicting_txids: Set[str]) -> Set[Tuple[str, int]]:
        ignored: Set[Tuple[str, int]] = set()
        for txid in conflicting_txids:
            entry = self.transactions.get(txid)
            if not entry:
                continue
            for txin in entry.tx.vin:
                ignored.add((txin.prev_tx_hash.hex(), txin.prev_tx_index))
        return ignored

    def _signals_opt_in_rbf(self, tx: Transaction) -> bool:
        return any(int(txin.sequence) < 0xFFFFFFFE for txin in tx.vin)

    def _is_txid_replaceable(self, txid: str, _seen: Optional[Set[str]] = None) -> bool:
        entry = self.transactions.get(txid)
        if not entry:
            return False
        if self._signals_opt_in_rbf(entry.tx):
            return True
        seen = _seen or set()
        if txid in seen:
            return False
        seen.add(txid)
        for parent in self.unconfirmed_parents.get(txid, set()):
            if self._is_txid_replaceable(parent, seen):
                return True
        return False

    def _can_replace(
        self,
        conflicting_txids: Set[str],
        tx: Transaction,
        new_fee: int,
        new_vsize: int,
        new_fee_rate: float,
    ) -> bool:
        # Replace set includes conflicts + descendants (BIP125-like behavior).
        replacement_set = set(conflicting_txids)
        for txid in list(conflicting_txids):
            replacement_set.update(self._get_descendants(txid))

        if len(replacement_set) > 100:
            return False
        conflict_entries = [self.transactions[c] for c in replacement_set if c in self.transactions]
        if not conflict_entries:
            return True
        # BIP125 signaling may be inherited from unconfirmed ancestors.
        if any(not self._is_txid_replaceable(c) for c in conflicting_txids):
            return False

        # New transaction cannot introduce additional unconfirmed inputs unless those
        # parents are already part of the replacement set.
        old_unconfirmed_parents: Set[str] = set()
        for entry in conflict_entries:
            old_unconfirmed_parents.update(self.unconfirmed_parents.get(entry.txid, set()))
        for txin in tx.vin:
            parent = txin.prev_tx_hash.hex()
            if parent in self.transactions and parent not in replacement_set and parent not in old_unconfirmed_parents:
                return False

        old_fee = sum(e.fee for e in conflict_entries)
        old_total_vsize = sum(max(1, int(e.vsize)) for e in conflict_entries)
        old_pkg_rate = old_fee / max(1, old_total_vsize)
        incremental = self.policy.get_min_fee_for_vsize(max(1, new_vsize))
        if new_fee <= old_fee + incremental:
            return False
        if new_fee_rate <= old_pkg_rate:
            return False
        # Prevent replacing with exact same transaction bytes.
        for entry in conflict_entries:
            if entry.tx.serialize() == tx.serialize():
                return False
        return True

    async def get_stats(self) -> Dict:
        min_fee_rate = self.transactions[self.tx_by_fee[-1]].fee_rate if self.tx_by_fee else 0
        max_fee_rate = self.transactions[self.tx_by_fee[0]].fee_rate if self.tx_by_fee else 0
        return {
            'size': len(self.transactions),
            'total_size': self.total_size,
            'total_vsize': self.total_vsize,
            'total_weight': self.total_weight,
            'total_fee': self.total_fee,
            'min_fee_rate': min_fee_rate,
            'max_fee_rate': max_fee_rate,
            'avg_fee_rate': self.total_fee / self.total_vsize if self.total_vsize else 0,
            'limits': self.limits.get_stats(),
        }
