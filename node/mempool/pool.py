"""Transaction pool management."""

import time
import asyncio
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from shared.core.transaction import Transaction
from shared.consensus.buried_deployments import HARDFORK_TX_V2, is_consensus_feature_active
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
        # Policy telemetry (P0 safety lock):
        # - reject reason counters
        # - eviction reason counters
        # - current minimum fee floor gauge (sat/vB)
        self.reject_reason_counts: Dict[str, int] = {}
        self.eviction_reason_counts: Dict[str, int] = {}
        self.min_fee_floor_rate: float = float(getattr(self.policy, "min_relay_fee", 0))
        self._min_fee_floor_half_life_secs: float = 10.0 * 60.0
        self._min_fee_floor_last_update: float = time.time()

    def _record_reject(self, reason: Optional[str]) -> str:
        text = str(reason or "unknown_reject")
        self.last_reject_reason = text
        self.reject_reason_counts[text] = int(self.reject_reason_counts.get(text, 0)) + 1
        return text

    def _record_eviction(self, reason: str, count: int = 1) -> None:
        text = str(reason or "unknown_eviction")
        self.eviction_reason_counts[text] = int(self.eviction_reason_counts.get(text, 0)) + int(max(0, count))

    def _decay_min_fee_floor(self, now: Optional[float] = None) -> None:
        ts = float(now if now is not None else time.time())
        base_floor = float(getattr(self.policy, "min_relay_fee", 0))
        elapsed = max(0.0, ts - float(self._min_fee_floor_last_update))
        if elapsed <= 0:
            return
        half_life = max(1.0, float(self._min_fee_floor_half_life_secs))
        # Exponential decay toward base floor.
        decay = 0.5 ** (elapsed / half_life)
        self.min_fee_floor_rate = float(base_floor) + (float(self.min_fee_floor_rate) - float(base_floor)) * decay
        self._min_fee_floor_last_update = ts

    def _effective_min_fee_floor_rate(self) -> float:
        self._decay_min_fee_floor()
        return max(
            float(getattr(self.policy, "min_relay_fee", 0)),
            float(self.min_fee_floor_rate),
        )

    def _update_min_fee_floor(
        self,
        candidate_fee_rate: Optional[float] = None,
        *,
        under_pressure: bool = False,
    ) -> None:
        self._decay_min_fee_floor()
        base_floor = float(getattr(self.policy, "min_relay_fee", 0))
        if candidate_fee_rate is None:
            self.min_fee_floor_rate = max(float(self.min_fee_floor_rate), float(base_floor))
            return
        try:
            candidate = float(candidate_fee_rate)
            if under_pressure:
                # Rolling floor bump while saturated; bias upward to deter immediate re-spam.
                candidate *= 1.10
            self.min_fee_floor_rate = max(
                float(base_floor),
                float(self.min_fee_floor_rate),
                float(candidate),
            )
        except (TypeError, ValueError):
            self.min_fee_floor_rate = max(float(self.min_fee_floor_rate), float(base_floor))

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
            self._record_reject("package_topology_invalid")
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_topology_invalid",
            }
        if len(ordered) > self.limits.max_package_count:
            self._record_reject("package_too_many_transactions")
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_too_many_transactions",
            }
        package_weight = sum(calculate_transaction_weight(tx) for tx in ordered)
        if package_weight > self.limits.max_package_weight:
            self._record_reject("package_too_heavy")
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_too_heavy",
            }
        package_vsize = sum(self._virtual_size_from_weight(calculate_transaction_weight(tx)) for tx in ordered)
        package_fee = self._estimate_package_fee(ordered)
        if package_fee is None:
            self._record_reject("package_missing_parents")
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_missing_parents",
            }
        package_fee_rate = (float(package_fee) / float(package_vsize)) if package_vsize > 0 else 0.0
        effective_floor = self._effective_min_fee_floor_rate()
        if package_fee_rate < effective_floor:
            self._record_reject("package_fee_too_low")
            return {
                "accepted": False,
                "added": [],
                "reject-reason": "package_fee_too_low",
            }

        added: List[str] = []
        for tx in ordered:
            ok = await self.add_transaction(
                tx,
                source_peer=source_peer,
                package_min_fee_rate=package_fee_rate,
            )
            if not ok:
                for txid in reversed(added):
                    await self.remove_transaction(txid, include_descendants=True)
                self._record_reject(self.last_reject_reason or "package_rejected")
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

    async def add_transaction(
        self,
        tx: Transaction,
        source_peer: Optional[str] = None,
        package_min_fee_rate: Optional[float] = None,
    ) -> bool:
        async with self._lock:
            txid = tx.txid().hex()
            if txid in self.transactions:
                logger.debug(f"Transaction {txid[:16]} already in mempool")
                self._record_reject("already_in_mempool")
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
                self._record_reject("rbf_policy")
                return False

            if not await self._validate_transaction(tx, ignored_spent_outpoints=ignored_spent):
                # _validate_transaction logs specifics and may set a reject reason.
                self._record_reject(self.last_reject_reason or "validation_failed")
                return False
            if not self.policy.is_standard(tx):
                logger.debug(f"Transaction {txid[:16]} not standard")
                self._record_reject("non_standard")
                return False
            required_floor_rate = self._effective_min_fee_floor_rate()
            if float(fee_rate) < float(required_floor_rate):
                if package_min_fee_rate is not None and float(package_min_fee_rate) >= float(required_floor_rate):
                    # CPFP package admission: allow low-fee individual tx if package
                    # aggregate feerate clears active rolling floor.
                    pass
                else:
                    logger.debug(f"Transaction {txid[:16]} fee too low")
                    self._record_reject("fee_too_low")
                    return False
            parents = await self._check_dependencies(tx)
            if parents is None:
                logger.debug(f"Transaction {txid[:16]} has missing parents")
                self._record_reject("missing_parents")
                return False
            ancestors = self._collect_ancestors(parents)
            if len(ancestors) > self.limits.max_ancestors:
                logger.debug("Too many ancestors")
                self._record_reject("too_many_ancestors")
                return False
            ancestor_package_vsize = vsize + sum(
                self.transactions[a].vsize for a in ancestors if a in self.transactions
            )
            if ancestor_package_vsize > self.limits.max_ancestor_size_vbytes:
                logger.debug("Ancestor package too large")
                self._record_reject("ancestor_package_too_large")
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
                self._record_reject("mempool_limits")
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
                            self._record_reject("too_many_descendants")
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
                            self._record_reject("descendant_package_too_large")
                            self._remove_transaction_nolock(txid, include_descendants=False)
                            return False
            logger.info(f"Added transaction {txid[:16]} to mempool (fee: {fee} sat, size: {size})")
        await self._broadcast_tx_inv(tx, source_peer)
        # Clear last_reject_reason on success
        self.last_reject_reason = None
        self._update_min_fee_floor()
        return True

    async def _evict_for_space(self, need_size: int, need_weight: int, incoming_fee_rate: float) -> None:
        """Evict low-fee transactions until limits can accept incoming tx."""
        if not self.transactions:
            return
        # Lowest effective package feerate first (tx + descendants), with deterministic
        # tie-breakers to avoid eviction instability.
        txids = sorted(self.transactions.keys(), key=self._eviction_rank)
        for txid in txids:
            if self.limits.can_accept(
                need_size, need_weight, len(self.transactions), self.total_size, self.total_weight
            ):
                break
            entry = self.transactions.get(txid)
            if not entry:
                continue
            pkg_fee_rate, _pkg_vsize, _pkg_size = self._descendant_package_stats(txid)
            # Do not evict if incoming doesn't outbid the candidate.
            if incoming_fee_rate <= pkg_fee_rate:
                self._update_min_fee_floor(pkg_fee_rate, under_pressure=True)
                break
            removed = self._remove_transaction_nolock(txid, include_descendants=True)
            self._record_eviction("mempool_space", len(removed))
            self._update_min_fee_floor(pkg_fee_rate, under_pressure=True)

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
        next_height = int(self.chainstate.get_best_height()) + 1

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
                if any(
                    not self._is_consensus_tx_valid_for_height(
                        self.transactions[t].tx, next_height, set_reason=False
                    )
                    for t in pkg_ids
                    if t in self.transactions
                ):
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
                    self._record_eviction("reorg_revalidation_invalid", 1)
                    continue
                parents = await self._check_dependencies(tx)
                if parents is None:
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    self._record_eviction("reorg_missing_parents", 1)
                    continue
                if not self.policy.is_standard(tx):
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    self._record_eviction("reorg_non_standard", 1)
                    continue
                fee = await self._calculate_fee(tx)
                tx_vsize = self._virtual_size_from_weight(calculate_transaction_weight(tx))
                if fee < self.policy.get_min_fee_for_vsize(tx_vsize):
                    removed.extend(self._remove_transaction_nolock(txid, include_descendants=True))
                    self._record_eviction("reorg_fee_too_low", 1)
                    floor = (fee / tx_vsize) if tx_vsize > 0 else 0.0
                    self._update_min_fee_floor(floor)
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

        next_height = int(self.chainstate.get_best_height()) + 1
        if not self._is_consensus_tx_valid_for_height(tx, next_height):
            self.last_reject_reason = self.last_reject_reason or "consensus_rule_invalid"
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

    def _is_consensus_tx_valid_for_height(
        self,
        tx: Transaction,
        height: int,
        set_reason: bool = True,
    ) -> bool:
        params = getattr(self.chainstate, "params", None)
        if params is None:
            return True

        # Fast-path custom hard-fork gate used in this repo.
        if is_consensus_feature_active(params, HARDFORK_TX_V2, int(height)):
            if int(getattr(tx, "version", 1)) < 2:
                if set_reason:
                    self.last_reject_reason = "consensus_tx_version_too_low"
                return False

        # Keep parity with shared consensus rules when available.
        rules = getattr(self.chainstate, "rules", None)
        validate_tx = getattr(rules, "validate_transaction", None)
        if callable(validate_tx):
            try:
                validate_tx(tx, int(height))
            except Exception:
                if set_reason:
                    self.last_reject_reason = "consensus_rule_invalid"
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

    def _descendant_package_stats(self, txid: str) -> Tuple[float, int, int]:
        """Return (package_fee_rate, package_vsize, package_count) for tx+descendants."""
        members = {txid}
        members.update(self._get_descendants(txid))
        package_fee = 0
        package_vsize = 0
        package_count = 0
        for member in members:
            entry = self.transactions.get(member)
            if not entry:
                continue
            package_fee += int(entry.fee)
            package_vsize += int(entry.vsize)
            package_count += 1
        if package_vsize <= 0:
            return 0.0, 0, package_count
        return float(package_fee) / float(package_vsize), int(package_vsize), int(package_count)

    def _eviction_rank(self, txid: str) -> Tuple[float, float, int, str]:
        """Deterministic eviction ordering with package impact awareness."""
        entry = self.transactions.get(txid)
        if not entry:
            return (float("inf"), float("inf"), 0, str(txid))
        package_rate, package_vsize, _package_count = self._descendant_package_stats(txid)
        direct_rate = float(entry.fee_rate)
        # Evict lowest package feerate first, then lowest direct feerate.
        # If still tied, evict larger package first (frees more room), then txid.
        return (
            float(package_rate),
            float(direct_rate),
            -int(package_vsize),
            str(txid),
        )

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
        ordered_ids: Set[str] = set()

        while unresolved:
            progressed = False
            for txid in sorted(unresolved):
                tx = tx_by_id[txid]
                deps: Set[str] = set()
                for txin in tx.vin:
                    parent = txin.prev_tx_hash.hex()
                    if parent in tx_by_id:
                        deps.add(parent)
                if deps.issubset(ordered_ids):
                    ordered.append(tx)
                    ordered_ids.add(txid)
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
        replacement_total_vsize = sum(max(1, int(e.vsize)) for e in conflict_entries)
        if replacement_total_vsize > max(1, int(self.limits.max_package_weight // 4)):
            # Bound replacement complexity to package-size policy budget.
            return False
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

    def _estimate_package_fee(self, ordered: List[Transaction]) -> Optional[int]:
        """Estimate aggregate package fee using in-package dependency outputs."""
        staged_outputs: Dict[Tuple[str, int], int] = {}
        total_fee = 0
        for tx in ordered:
            txid = tx.txid().hex()
            total_in = 0
            for txin in tx.vin:
                prev_txid = txin.prev_tx_hash.hex()
                prev_index = int(txin.prev_tx_index)
                outpoint = (prev_txid, prev_index)
                if outpoint in staged_outputs:
                    total_in += int(staged_outputs[outpoint])
                    continue
                utxo = self.chainstate.get_utxo(prev_txid, prev_index)
                if not utxo and prev_txid in self.transactions:
                    parent_entry = self.transactions.get(prev_txid)
                    if parent_entry and 0 <= prev_index < len(parent_entry.tx.vout):
                        utxo = {"value": int(parent_entry.tx.vout[prev_index].value)}
                if not utxo:
                    return None
                total_in += int(utxo.get("value", 0))
            total_out = sum(int(txout.value) for txout in tx.vout)
            total_fee += int(total_in - total_out)
            for idx, txout in enumerate(tx.vout):
                staged_outputs[(txid, idx)] = int(txout.value)
        return int(total_fee)

    async def get_stats(self) -> Dict:
        min_fee_rate = self.transactions[self.tx_by_fee[-1]].fee_rate if self.tx_by_fee else 0
        max_fee_rate = self.transactions[self.tx_by_fee[0]].fee_rate if self.tx_by_fee else 0
        self._update_min_fee_floor()
        return {
            'size': len(self.transactions),
            'total_size': self.total_size,
            'total_vsize': self.total_vsize,
            'total_weight': self.total_weight,
            'total_fee': self.total_fee,
            'min_fee_rate': min_fee_rate,
            'max_fee_rate': max_fee_rate,
            'avg_fee_rate': self.total_fee / self.total_vsize if self.total_vsize else 0,
            'policy': {
                'min_fee_floor_rate': float(self.min_fee_floor_rate),
                'reject_reason_counts': dict(self.reject_reason_counts),
                'eviction_reason_counts': dict(self.eviction_reason_counts),
            },
            'limits': self.limits.get_stats(),
        }

    def get_policy_thresholds(self) -> Dict[str, object]:
        """Return operator-facing mempool policy/limit thresholds."""
        self._update_min_fee_floor()
        policy_summary = self.policy.get_policy_summary() if hasattr(self.policy, "get_policy_summary") else {}
        return {
            "policy": dict(policy_summary),
            "limits": dict(self.limits.get_stats()),
            "rolling_fee_floor_rate": float(self.min_fee_floor_rate),
            "rolling_fee_floor_half_life_secs": float(self._min_fee_floor_half_life_secs),
            "last_reject_reason": self.last_reject_reason,
        }

    def get_eviction_snapshot(self, limit: int = 10) -> Dict[str, object]:
        """Return current eviction ranking candidates for operator diagnostics."""
        count = max(1, int(limit))
        candidates = []
        ranked = sorted(self.transactions.keys(), key=self._eviction_rank)
        for txid in ranked[:count]:
            entry = self.transactions.get(txid)
            if not entry:
                continue
            pkg_rate, pkg_vsize, pkg_count = self._descendant_package_stats(txid)
            candidates.append(
                {
                    "txid": txid,
                    "fee_rate": float(entry.fee_rate),
                    "package_fee_rate": float(pkg_rate),
                    "package_vsize": int(pkg_vsize),
                    "package_count": int(pkg_count),
                    "age_seconds": float(entry.age),
                }
            )
        return {
            "candidate_count": len(candidates),
            "candidates": candidates,
            "totals": {
                "txs": int(len(self.transactions)),
                "size_bytes": int(self.total_size),
                "vsize": int(self.total_vsize),
                "weight": int(self.total_weight),
            },
        }
