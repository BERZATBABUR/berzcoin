"""Main entry point for BerzCoin node."""

import argparse
import asyncio
import hashlib
import ipaddress
import json
import signal
import sys
import time
from typing import Any, Optional, Dict, List

from shared.utils.logging import get_logger, setup_logging
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool
from node.mempool.limits import MempoolLimits
from node.mempool.policy import MempoolPolicy
from node.mining.miner import MiningNode
from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from node.p2p.dns_seeds import DNSSeeds
from node.rpc.handlers.blockchain import BlockchainHandlers
from node.rpc.handlers.control import ControlHandlers
from node.rpc.handlers.mempool import MempoolHandlers
from node.rpc.handlers.mining import MiningHandlers
from node.rpc.handlers.mining_control import MiningControlHandlers
from node.rpc.handlers.wallet import WalletHandlers
from node.rpc.handlers.wallet_control import WalletControlHandlers
from node.rpc.server import RPCServer
from node.storage.blocks_store import BlocksStore
from node.storage.db import Database
from node.storage.mempool_store import MempoolStore
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore
from shared.core.transaction import Transaction
from shared.core.block import Block
from shared.protocol.messages import (
    InvMessage,
    GetDataMessage,
    GetHeadersMessage,
    HeadersMessage,
    AddrMessage,
    TxMessage,
    BlockMessage,
    PingMessage,
    PongMessage,
    SendCmpctMessage,
    CmpctBlockMessage,
    GetBlockTxnMessage,
    BlockTxnMessage,
    compact_shortid,
)
from shared.consensus.buried_deployments import HARDFORK_TX_V2
from node.p2p.orphanage import Orphanage
from node.wallet.simple_wallet import SimpleWalletManager
from .health import HealthChecker
from .metrics import MetricsCollector
from .components import ComponentManager
from .config import Config
from .modes import ModeManager

logger = get_logger()


class BerzCoinNode:
    """Main BerzCoin node."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = Config(config_path)
        self.mode_manager = ModeManager(self.config)
        self.component_manager = ComponentManager()

        self.network: str = self.config.get("network", "mainnet")
        self.db: Optional[Database] = None
        self.blocks_store: Optional[BlocksStore] = None
        self.utxo_store: Optional[UTXOStore] = None
        self.chainstate: Optional[ChainState] = None
        self.connman: Optional[ConnectionManager] = None
        self.mempool: Optional[Mempool] = None
        self.mempool_store: Optional[MempoolStore] = None
        self.simple_wallet_manager: Optional[SimpleWalletManager] = None
        self.miner: Optional[MiningNode] = None
        self.rpc_server: Optional[RPCServer] = None
        self.dashboard: Any = None
        self.health_checker: Optional[HealthChecker] = None
        self.metrics_collector: Optional[MetricsCollector] = None
        self.block_sync: Any = None
        self.orphanage = Orphanage(
            max_orphans=int(self.config.get("max_orphans", 200)),
            max_age=int(self.config.get("max_orphan_age_secs", 7200)),
        )
        self._known_txs: set[str] = set()
        self._known_blocks: set[str] = set()
        self._peer_msg_window: Dict[str, tuple[float, int]] = {}
        self._max_msgs_per_sec = int(self.config.get("p2p_max_msgs_per_sec", 300))
        self._pending_compact_blocks: Dict[str, Dict[str, Any]] = {}
        self._compact_max_missing_indexes = int(
            self.config.get("compact_max_missing_indexes", 32)
        )

        self.running = False
        self.sync_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Initialize node subsystems."""
        logger.info("Initializing BerzCoin node...")

        if not self.config.validate():
            return False

        setup_logging(
            level=("DEBUG" if self.config.get("debug") else "INFO"),
            log_file=str(
                self.config.get_datadir() / self.config.get("logfile")
            ),
        )

        if not await self._init_database():
            return False
        if not await self._init_chainstate():
            return False
        if not self._hardfork_guardrails_ok():
            return False
        if not await self._init_mempool():
            return False
        if not await self._init_p2p():
            return False
        if not await self._init_wallet():
            return False
        if not await self._init_mining():
            return False
        if not await self._init_rpc():
            return False
        self.health_checker = HealthChecker(self)
        self.metrics_collector = MetricsCollector(self)

        logger.info("Node initialized successfully")
        return True

    def _hardfork_guardrails_ok(self) -> bool:
        """Refuse startup if node consensus version is below active hard-fork requirement."""
        if not bool(self.config.get("enforce_hardfork_guardrails", True)):
            return True
        if self.chainstate is None:
            return True

        params = getattr(self.chainstate, "params", None)
        if params is None:
            return True

        custom = getattr(params, "custom_activation_heights", {}) or {}
        activation_height = custom.get(HARDFORK_TX_V2)
        if activation_height is None:
            return True

        # First hard-fork profile currently requires consensus version 2.
        required_consensus_version = 2
        node_consensus_version = int(self.config.get("node_consensus_version", 1))
        tip_height = int(self.chainstate.get_best_height())

        if tip_height >= int(activation_height) and node_consensus_version < required_consensus_version:
            logger.error(
                "Startup blocked by hard-fork guardrail: tip=%s activation=%s "
                "requires node_consensus_version>=%s (current=%s)",
                tip_height,
                activation_height,
                required_consensus_version,
                node_consensus_version,
            )
            return False
        return True

    async def start(self) -> None:
        """Start node services."""
        logger.info("Starting BerzCoin node...")
        self.running = True
        # Ensure expected subsystems are initialized for type checkers
        assert self.chainstate is not None

        if self.connman:
            await self.connman.start()

        if self.rpc_server:
            await self.rpc_server.start()

        # Bootstrap if chain is empty
        if self.chainstate.get_best_height() == -1:
            logger.info("Chain empty, running bootstrap...")
            from node.app.bootstrap import NodeBootstrap

            bootstrap = NodeBootstrap(
                self.chainstate,
                self.connman,
                self.utxo_store,
            )
            try:
                coro = bootstrap.sync_full_chain(replay_rebuild=True)
                await asyncio.wait_for(coro, timeout=300)
            except asyncio.TimeoutError:
                logger.warning("Bootstrap timeout")

        if self.connman:
            self.sync_task = asyncio.create_task(self._run_sync_loop())

        # Auto-start mining if configured
        if self.miner and self.config.get("autominer"):
            await self.miner.start_mining(self.config.get("miningaddress"))

        logger.info("Node started")
        await self._wait_for_shutdown()

    async def stop(self) -> None:
        """Stop node services."""
        logger.info("Stopping BerzCoin node...")
        self.running = False

        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass

        if self.miner:
            await self.miner.stop_mining()

        if self.rpc_server:
            await self.rpc_server.stop()

        if self.dashboard:
            await self.dashboard.stop()

        if self.connman:
            await self.connman.stop()

        self._flush_mempool_to_disk()

        if self.db:
            self.db.disconnect()

        logger.info("Node stopped")

    async def on_transaction(
        self,
        tx: Transaction,
        source_peer: Optional[str] = None,
        relay: bool = True,
    ) -> tuple[bool, str, Optional[str]]:
        """Validation-first transaction intake owned by the node."""
        txid = tx.txid().hex()
        if not self.mempool:
            return False, txid, "mempool_unavailable"

        accepted = await self.mempool.add_transaction(tx, source_peer=source_peer)
        if not accepted:
            return False, txid, self.mempool.last_reject_reason or "mempool_rejected"

        if relay and self.connman:
            inv = InvMessage(inventory=[(InvMessage.InvType.MSG_TX, tx.txid())])
            await self.connman.broadcast(
                "inv",
                inv.serialize(),
                exclude={source_peer} if source_peer else None,
            )
        self._remember_known_tx(txid)
        return True, txid, None

    async def on_block(
        self,
        block: Block,
        source_peer: Optional[str] = None,
        relay: bool = False,
    ) -> tuple[bool, str, Optional[str]]:
        """Single validation/acceptance pipeline for incoming blocks."""
        if not self.chainstate:
            return False, "", "chainstate_unavailable"
        block_hash = block.header.hash_hex()
        if self.chainstate.block_index.get_block(block_hash):
            return True, block_hash, "known"

        self.orphanage.cleanup_expired()

        prev_hash = block.header.prev_block_hash.hex()
        parent_entry = self.chainstate.block_index.get_block(prev_hash)
        if parent_entry is None:
            self.orphanage.add_orphan(block, source_peer=source_peer)
            return False, block_hash, "orphan"

        height = int(parent_entry.height) + 1
        prev_block = self.chainstate.get_block(prev_hash)
        best_hash = self.chainstate.get_best_block_hash()
        if best_hash == prev_hash:
            if not self.chainstate.validate_block_stateful(block, height):
                return False, block_hash, "stateful_validation_failed"
        else:
            try:
                self.chainstate.rules.validate_block(block, prev_block, height)
            except Exception:
                return False, block_hash, "consensus_rule_invalid"

        block_work = self.chainstate.chainwork.calculate_chain_work([block.header])
        chainwork_total = int(parent_entry.chainwork) + int(block_work)

        self.chainstate.blocks_store.write_block(block, height)
        self.chainstate.block_index.add_block(
            block, height, chainwork_total, update_best=False
        )
        self.chainstate.header_chain.add_header(block.header, height, chainwork_total)

        if best_hash == prev_hash:
            ok = await self._connect_as_new_tip(block, height, chainwork_total)
            if not ok:
                return False, block_hash, "connect_failed"
            if relay and self.connman:
                await self.connman.broadcast_block(block)
            self._remember_known_block(block_hash)
            await self._process_orphan_children(block_hash, relay=relay)
            return True, block_hash, None

        # Side-branch block
        if chainwork_total <= self.chainstate.get_best_chainwork():
            return True, block_hash, "stored_fork"

        # Heavier side branch -> attempt reorg.
        from node.chain.reorg import ReorgManager

        current_best = self.chainstate.block_index.get_block(best_hash) if best_hash else None
        candidate_best = self.chainstate.block_index.get_block(block_hash)
        if current_best is None or candidate_best is None:
            return False, block_hash, "reorg_missing_index_entry"

        reorg = ReorgManager(
            self.chainstate.utxo_store,
            self.chainstate.block_index,
            max_reorg_depth=int(self.config.get("max_reorg_depth", 144)),
        )
        if not reorg.can_reorganize(
            candidate_best,
            current_best,
            get_block_func=self.chainstate.get_block,
        ):
            return False, block_hash, "reorg_preflight_failed"
        ok, _disconnected, connected = reorg.reorganize(
            candidate_best,
            current_best,
            get_block_func=self.chainstate.get_block,
        )
        if not ok:
            return False, block_hash, "reorg_failed"

        self.chainstate.set_best_block(
            candidate_best.block_hash,
            candidate_best.height,
            candidate_best.chainwork,
        )
        if self.mempool:
            for connected_block in connected:
                await self.mempool.handle_connected_block(connected_block)
        if relay and self.connman:
            await self.connman.broadcast_block(block)
        self._remember_known_block(block_hash)
        await self._process_orphan_children(block_hash, relay=relay)
        return True, block_hash, None

    async def _connect_as_new_tip(self, block: Block, height: int, chainwork_total: int) -> bool:
        from node.validation.connect import ConnectBlock

        connect = ConnectBlock(
            self.chainstate.utxo_store,
            self.chainstate.block_index,
            network=self.chainstate.params.get_network_name(),
        )
        if not connect.connect(block):
            return False
        block_hash = block.header.hash_hex()
        self.chainstate.set_best_block(block_hash, height, chainwork_total)
        if self.mempool:
            await self.mempool.handle_connected_block(block)
        return True

    async def _process_orphan_children(self, parent_hash: str, relay: bool) -> None:
        while True:
            children = self.orphanage.get_children(parent_hash)
            if not children:
                return
            progress = False
            for child in children:
                child_hash = child.header.hash_hex()
                self.orphanage.remove_orphan(child_hash)
                accepted, _, reason = await self.on_block(
                    child, source_peer=None, relay=relay
                )
                if accepted:
                    parent_hash = child_hash
                    progress = True
                elif reason == "orphan":
                    # Parent still missing from this branch.
                    self.orphanage.add_orphan(child, source_peer=None)
            if not progress:
                return

    def _remember_known_tx(self, txid: str) -> None:
        self._known_txs.add(txid)
        if len(self._known_txs) > 200_000:
            self._known_txs.clear()

    def _remember_known_block(self, block_hash: str) -> None:
        self._known_blocks.add(block_hash)
        if len(self._known_blocks) > 50_000:
            self._known_blocks.clear()

    def _build_compact_candidate(
        self, cmpct_msg: CmpctBlockMessage
    ) -> tuple[Dict[int, bytes], List[int], int]:
        prefilled: Dict[int, bytes] = {
            int(index): bytes(tx_bytes)
            for index, tx_bytes in list(cmpct_msg.prefilled_txn or [])
        }
        total_txs = len(cmpct_msg.shortids) + len(prefilled)
        if total_txs <= 0:
            return {}, [], 0
        remaining_indexes = [i for i in range(total_txs) if i not in prefilled]
        if len(remaining_indexes) != len(cmpct_msg.shortids):
            return {}, list(range(total_txs)), total_txs

        tx_slots: Dict[int, bytes] = dict(prefilled)
        shortid_map: Dict[int, Optional[bytes]] = {}
        mempool_txs = getattr(self.mempool, "transactions", {}) if self.mempool else {}
        for entry in mempool_txs.values():
            tx = getattr(entry, "tx", None)
            if tx is None:
                continue
            sid = compact_shortid(cmpct_msg.header, cmpct_msg.nonce, tx.txid())
            tx_bytes = tx.serialize()
            if sid in shortid_map and shortid_map[sid] != tx_bytes:
                shortid_map[sid] = None
            elif sid not in shortid_map:
                shortid_map[sid] = tx_bytes

        missing: List[int] = []
        for pos, shortid in enumerate(cmpct_msg.shortids):
            index = remaining_indexes[pos]
            tx_bytes = shortid_map.get(int(shortid))
            if tx_bytes is None:
                missing.append(index)
                continue
            tx_slots[index] = bytes(tx_bytes)
        return tx_slots, missing, total_txs

    def _finalize_compact_block(
        self, header: bytes, tx_slots: Dict[int, bytes], total_txs: int
    ) -> Optional[Block]:
        if total_txs <= 0:
            return None
        if any(i not in tx_slots for i in range(total_txs)):
            return None
        payload = bytearray(header)
        from shared.core.serialization import Serializer as _S

        payload.extend(_S.write_varint(total_txs))
        for i in range(total_txs):
            payload.extend(tx_slots[i])
        try:
            block, _ = Block.deserialize(bytes(payload))
            return block
        except Exception:
            return None

    async def _request_full_block_fallback(self, peer, block_hash: bytes) -> None:
        try:
            await peer.send_getdata(InvMessage.InvType.MSG_BLOCK, block_hash)
        except Exception:
            pass

    async def _handle_stale_compact_requests(self) -> None:
        if self.block_sync is None or self.connman is None:
            return
        stale = self.block_sync.get_stale_compact_requests()
        for block_hash_hex in stale:
            pending = self._pending_compact_blocks.get(block_hash_hex)
            if not pending:
                continue
            peer_addr = str(pending.get("peer_addr", ""))
            peer = self.connman.get_peer(peer_addr) if peer_addr else None
            block_hash = pending.get("block_hash")
            if peer is not None and isinstance(block_hash, (bytes, bytearray)):
                await self._request_full_block_fallback(peer, bytes(block_hash))
                peer.record_compact_result(False)

    def _peer_rate_limited(self, peer_addr: str) -> bool:
        now = asyncio.get_event_loop().time()
        window_start, count = self._peer_msg_window.get(peer_addr, (now, 0))
        if now - window_start >= 1.0:
            self._peer_msg_window[peer_addr] = (now, 1)
            return False
        count += 1
        self._peer_msg_window[peer_addr] = (window_start, count)
        return count > self._max_msgs_per_sec

    async def _ensure_block_sync(self):
        from node.p2p.sync import BlockSync

        if self.block_sync is None:
            self.block_sync = BlockSync(
                self.chainstate,
                mempool=self.mempool,
                block_handler=self.on_block,
                getdata_batch_size=int(self.config.get("sync_getdata_batch_size", 128)),
                block_request_timeout_secs=int(
                    self.config.get("sync_block_request_timeout_secs", 30)
                ),
            )

    @staticmethod
    def _split_host_port(address: str, default_port: int) -> tuple[str, int]:
        raw = (address or "").strip()
        if not raw:
            return "", int(default_port)
        if raw.startswith("["):
            end = raw.find("]")
            if end > 0:
                host = raw[1:end]
                if len(raw) > end + 2 and raw[end + 1] == ":":
                    try:
                        return host, int(raw[end + 2 :])
                    except ValueError:
                        return host, int(default_port)
                return host, int(default_port)
        if raw.count(":") > 1:
            return raw, int(default_port)
        if ":" in raw:
            host, port = raw.rsplit(":", 1)
            try:
                return host, int(port)
            except ValueError:
                return host, int(default_port)
        return raw, int(default_port)

    @staticmethod
    def _host_to_addr_bytes(host: str) -> Optional[bytes]:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return None
        if isinstance(ip, ipaddress.IPv4Address):
            return b"\x00" * 10 + b"\xff\xff" + ip.packed
        return ip.packed

    @staticmethod
    def _addr_bytes_to_host(ip_bytes: bytes) -> Optional[str]:
        if len(ip_bytes) != 16:
            return None
        if ip_bytes.startswith(b"\x00" * 10 + b"\xff\xff"):
            return str(ipaddress.IPv4Address(ip_bytes[12:16]))
        return str(ipaddress.IPv6Address(ip_bytes))

    async def _on_p2p_message(self, peer, command: str, payload: bytes) -> None:
        """Handle inbound P2P messages with validation-first relay behavior."""
        if self.connman and self.connman.peer_scores.is_banned(peer.address):
            try:
                await peer.disconnect()
            except Exception:
                pass
            return
        if self._peer_rate_limited(peer.address):
            if self.connman:
                self.connman.peer_scores.record_bad(peer.address, "msg_rate_limit")
            return

        try:
            if command == "sendcmpct":
                msg, _ = SendCmpctMessage.deserialize(payload)
                peer.prefers_compact_blocks = bool(msg.announce)
                peer.compact_block_version = int(msg.version)
                return

            if command == "getaddr":
                if not self.connman:
                    return
                default_port = int(self.config.get("port", 8333))
                candidates = list(self.connman.addrman.get_addresses(1000))
                candidates.extend(list(self.connman.peers.keys()))
                relayed: list[dict[str, Any]] = []
                seen: set[str] = set()
                now = int(time.time())
                for addr in candidates:
                    if addr in seen:
                        continue
                    seen.add(addr)
                    host, port = self._split_host_port(addr, default_port)
                    ip_bytes = self._host_to_addr_bytes(host)
                    if not ip_bytes:
                        continue
                    if not (1 <= int(port) <= 65535):
                        continue
                    relayed.append(
                        {
                            "time": now,
                            "services": 1,
                            "ip": ip_bytes,
                            "port": int(port),
                        }
                    )
                    if len(relayed) >= 1000:
                        break
                await peer.send_message("addr", AddrMessage(addresses=relayed).serialize())
                return

            if command == "addr":
                if not self.connman:
                    return
                msg, _ = AddrMessage.deserialize(payload)
                relayed_addrs: list[str] = []
                default_port = int(self.config.get("port", 8333))
                for entry in msg.addresses[:1000]:
                    host = self._addr_bytes_to_host(entry.get("ip", b""))
                    if not host:
                        continue
                    port = int(entry.get("port", default_port))
                    if not (1 <= port <= 65535):
                        continue
                    if ":" in host:
                        relayed_addrs.append(f"[{host}]:{port}")
                    else:
                        relayed_addrs.append(f"{host}:{port}")
                added = self.connman.filter_and_add_addrs(peer.address, relayed_addrs)
                if added:
                    logger.debug("Accepted %s relayed addr(s) from %s", added, peer.address)
                return

            if command == "inv":
                inv, _ = InvMessage.deserialize(payload)
                max_inv = 2000
                inventory = inv.inventory
                if len(inventory) > max_inv:
                    if self.connman:
                        self.connman.peer_scores.record_bad(peer.address, "relay_spam")
                    inventory = inventory[:max_inv]
                seen: set[tuple[int, bytes]] = set()
                for inv_type, inv_hash in inventory:
                    key = (int(inv_type), bytes(inv_hash))
                    if key in seen:
                        continue
                    seen.add(key)
                    if inv_type == InvMessage.InvType.MSG_TX:
                        txid = inv_hash.hex()
                        if txid in self._known_txs:
                            continue
                        if self.mempool and await self.mempool.get_transaction(txid):
                            self._remember_known_tx(txid)
                            continue
                        await peer.send_getdata(inv_type, inv_hash)
                    elif inv_type == InvMessage.InvType.MSG_BLOCK:
                        block_hash = inv_hash[::-1].hex()
                        if block_hash in self._known_blocks:
                            continue
                        if self.chainstate and self.chainstate.block_index.get_block(block_hash):
                            self._remember_known_block(block_hash)
                            continue
                        await peer.send_getdata(inv_type, inv_hash)
                    elif inv_type == InvMessage.InvType.MSG_CMPCT_BLOCK:
                        await peer.send_getdata(inv_type, inv_hash)
                    elif self.connman:
                        self.connman.peer_scores.record_bad(peer.address, "protocol_violation")
                return

            if command == "tx":
                tx_msg, _ = TxMessage.deserialize(payload)
                tx, _ = Transaction.deserialize(tx_msg.transaction)
                txid = tx.txid().hex()
                if txid in self._known_txs:
                    return
                accepted, _, _reason = await self.on_transaction(
                    tx, source_peer=peer.address, relay=True
                )
                if accepted:
                    self._remember_known_tx(txid)
                return

            if command == "block":
                blk_msg, _ = BlockMessage.deserialize(payload)
                block, _ = Block.deserialize(blk_msg.block)
                block_hash = block.header.hash_hex()
                if self.block_sync is not None:
                    self.block_sync.resolve_compact_request(block_hash)
                self._pending_compact_blocks.pop(block_hash, None)
                if block_hash in self._known_blocks:
                    return
                accepted, _, _reason = await self.on_block(
                    block, source_peer=peer.address, relay=True
                )
                if accepted:
                    self._remember_known_block(block_hash)
                return

            if command == "cmpctblock":
                cmpct_msg, _ = CmpctBlockMessage.deserialize(payload)
                compact_block_hash = cmpct_msg.block_hash()
                block_hash_hex = compact_block_hash[::-1].hex()
                if block_hash_hex in self._known_blocks:
                    peer.record_compact_result(True)
                    return
                if self.chainstate and self.chainstate.block_index.get_block(block_hash_hex):
                    self._remember_known_block(block_hash_hex)
                    peer.record_compact_result(True)
                    return
                await self._ensure_block_sync()
                tx_slots, missing_indexes, total_txs = self._build_compact_candidate(cmpct_msg)
                if total_txs > 0 and not missing_indexes:
                    block = self._finalize_compact_block(
                        cmpct_msg.header, tx_slots, total_txs
                    )
                    if block is not None:
                        accepted, _, _reason = await self.on_block(
                            block, source_peer=peer.address, relay=True
                        )
                        if accepted:
                            self._remember_known_block(block_hash_hex)
                            peer.record_compact_result(True)
                            self.block_sync.resolve_compact_request(block_hash_hex)
                            self._pending_compact_blocks.pop(block_hash_hex, None)
                            return

                self._pending_compact_blocks[block_hash_hex] = {
                    "peer_addr": peer.address,
                    "block_hash": compact_block_hash,
                    "header": cmpct_msg.header,
                    "nonce": int(cmpct_msg.nonce),
                    "tx_slots": tx_slots,
                    "total_txs": int(total_txs),
                    "missing_indexes": list(missing_indexes),
                    "requested_indexes": list(missing_indexes),
                    "created_at": asyncio.get_event_loop().time(),
                }
                if missing_indexes and len(missing_indexes) <= self._compact_max_missing_indexes:
                    self.block_sync.register_compact_request(
                        block_hash_hex, peer.address, "getblocktxn"
                    )
                    await peer.send_getblocktxn(compact_block_hash, missing_indexes)
                    return

                # Too many unknown transactions: fallback directly to full block.
                self.block_sync.register_compact_request(
                    block_hash_hex, peer.address, "getdata"
                )
                peer.record_compact_result(False)
                await self._request_full_block_fallback(peer, compact_block_hash)
                return

            if command == "getblocktxn":
                if not self.chainstate:
                    return
                req, _ = GetBlockTxnMessage.deserialize(payload)
                block_hash_hex = req.block_hash[::-1].hex()
                block = self.chainstate.get_block(block_hash_hex)
                if not block:
                    return
                txs: List[bytes] = []
                for idx in req.indexes:
                    i = int(idx)
                    if 0 <= i < len(block.transactions):
                        txs.append(block.transactions[i].serialize())
                await peer.send_blocktxn(req.block_hash, txs)
                return

            if command == "blocktxn":
                msg, _ = BlockTxnMessage.deserialize(payload)
                block_hash_hex = msg.block_hash[::-1].hex()
                pending = self._pending_compact_blocks.get(block_hash_hex)
                if not pending:
                    return
                requested = list(pending.get("requested_indexes", []) or [])
                if len(requested) != len(msg.transactions):
                    self.block_sync.register_compact_request(
                        block_hash_hex, peer.address, "getdata"
                    )
                    peer.record_compact_result(False)
                    await self._request_full_block_fallback(peer, msg.block_hash)
                    return
                tx_slots = dict(pending.get("tx_slots", {}))
                for index, tx_bytes in zip(requested, msg.transactions):
                    tx_slots[int(index)] = bytes(tx_bytes)
                block = self._finalize_compact_block(
                    bytes(pending.get("header", b"")),
                    tx_slots,
                    int(pending.get("total_txs", 0)),
                )
                if block is None:
                    self.block_sync.register_compact_request(
                        block_hash_hex, peer.address, "getdata"
                    )
                    peer.record_compact_result(False)
                    await self._request_full_block_fallback(peer, msg.block_hash)
                    return
                accepted, _, _reason = await self.on_block(
                    block, source_peer=peer.address, relay=True
                )
                self.block_sync.resolve_compact_request(block_hash_hex)
                self._pending_compact_blocks.pop(block_hash_hex, None)
                if accepted:
                    self._remember_known_block(block_hash_hex)
                    peer.record_compact_result(True)
                    return
                peer.record_compact_result(False)
                return

            if command == "getdata":
                req, _ = GetDataMessage.deserialize(payload)
                max_getdata = 1024
                inventory = req.inventory
                if len(inventory) > max_getdata:
                    if self.connman:
                        self.connman.peer_scores.record_bad(peer.address, "relay_spam")
                    inventory = inventory[:max_getdata]
                for inv_type, inv_hash in inventory:
                    if inv_type == InvMessage.InvType.MSG_TX and self.mempool:
                        tx = await self.mempool.get_transaction(inv_hash.hex())
                        if tx:
                            msg = TxMessage(transaction=tx.serialize())
                            await peer.send_message("tx", msg.serialize())
                    elif inv_type == InvMessage.InvType.MSG_BLOCK:
                        block_hash = inv_hash[::-1].hex()
                        block = self.chainstate.get_block(block_hash) if self.chainstate else None
                        if block:
                            msg = BlockMessage(block=block.serialize())
                            await peer.send_message("block", msg.serialize())
                    elif inv_type == InvMessage.InvType.MSG_CMPCT_BLOCK:
                        block_hash = inv_hash[::-1].hex()
                        block = self.chainstate.get_block(block_hash) if self.chainstate else None
                        if block:
                            cmpct = CmpctBlockMessage.from_block(block)
                            await peer.send_message("cmpctblock", cmpct.serialize())
                return

            if command == "getheaders":
                if not self.chainstate:
                    return
                req, _ = GetHeadersMessage.deserialize(payload)
                start_height = 0
                for locator_hash in req.block_locator_hashes:
                    h = self.chainstate.get_height(locator_hash.hex())
                    if h is not None:
                        start_height = int(h) + 1
                        break
                best = self.chainstate.get_best_height()
                headers: list[bytes] = []
                for h in range(start_height, min(best + 1, start_height + 2000)):
                    header = self.chainstate.get_header(h)
                    if not header:
                        continue
                    headers.append(header.serialize())
                await peer.send_message("headers", HeadersMessage(headers=headers).serialize())
                return

            if command == "getblocks":
                if not self.chainstate:
                    return
                req, _ = GetBlocksMessage.deserialize(payload)
                start_height = 0
                for locator_hash in req.block_locator_hashes:
                    h = self.chainstate.get_height(locator_hash.hex())
                    if h is not None:
                        start_height = int(h) + 1
                        break

                best = self.chainstate.get_best_height()
                max_inv = 500
                inventory: list[tuple[int, bytes]] = []
                for h in range(start_height, min(best + 1, start_height + max_inv)):
                    header = self.chainstate.get_header(h)
                    if not header:
                        continue
                    inventory.append((InvMessage.InvType.MSG_BLOCK, header.hash()))

                if inventory:
                    await peer.send_message("inv", InvMessage(inventory=inventory).serialize())
                return

            if command == "headers":
                await self._ensure_block_sync()
                headers_msg, _ = HeadersMessage.deserialize(payload)
                await self.block_sync.process_headers(peer, headers_msg.headers)
                return

            if command == "ping":
                ping, _ = PingMessage.deserialize(payload)
                await peer.send_message("pong", PongMessage(nonce=ping.nonce).serialize())
                return

        except Exception as e:
            if self.connman and command in {"inv", "getdata", "cmpctblock", "sendcmpct"}:
                self.connman.peer_scores.record_bad(peer.address, "protocol_violation")
            logger.debug("P2P message handling error (%s): %s", command, e)

    async def _run_sync_loop(self) -> None:
        """Run block synchronization loop."""
        assert self.chainstate is not None
        logger.info("Starting block sync loop")

        while self.running:
            try:
                if self.connman and self.connman.get_connected_count() > 0:
                    best_peer = self.connman.get_best_height_peer()

                    if best_peer:
                        cur = self.chainstate.get_best_height()
                        peer_height = best_peer.peer_height

                        if peer_height > cur:
                            logger.info(
                                "Syncing: %d / %d",
                                cur, peer_height,
                            )

                            from node.p2p.sync import BlockSync
                            if self.block_sync is None:
                                self.block_sync = BlockSync(
                                    self.chainstate,
                                    mempool=self.mempool,
                                    block_handler=self.on_block,
                                    getdata_batch_size=int(self.config.get("sync_getdata_batch_size", 128)),
                                    block_request_timeout_secs=int(
                                        self.config.get("sync_block_request_timeout_secs", 30)
                                    ),
                                )
                            await self.block_sync.sync_from_peer(best_peer)

                await self._handle_stale_compact_requests()

                await asyncio.sleep(max(1, int(self.config.get("sync_poll_interval_secs", 30))))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(max(1, int(self.config.get("sync_error_backoff_secs", 60))))

    async def _init_database(self) -> bool:
        """Initialize database."""
        datadir = self.config.get_datadir()
        network = self.config.get("network")
        self.db = Database(datadir, network)
        self.db.connect()

        migrations = Migrations(self.db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return True

    async def _init_chainstate(self) -> bool:
        """Initialize chainstate."""
        data_dir = str(self.config.get_datadir())
        # ensure database initialized for type checkers
        assert self.db is not None
        self.blocks_store = BlocksStore(
            self.db,
            data_dir,
            cache_size=int(self.config.get("blocks_cache_size", 100)),
        )
        self.utxo_store = UTXOStore(self.db)
        self.chainstate = ChainState(
            self.db,
            self.config.get_network_params(),
            data_dir,
            blocks_store=self.blocks_store,
            utxo_store=self.utxo_store,
        )
        self.chainstate.initialize()
        # Expose configured network name on chainstate for compatibility
        # with components (miner, tx builder) that expect `chainstate.network`.
        try:
            setattr(self.chainstate, "network", self.config.get("network"))
        except Exception:
            pass
        return True

    async def _init_mempool(self) -> bool:
        """Initialize mempool."""
        if self.mode_manager.is_light_node():
            self.mempool = None
            self.mempool_store = None
            return True
        # Ensure chainstate available for mempool
        assert self.chainstate is not None
        policy = MempoolPolicy()
        relay_fee = self.config.get("mempool_min_relay_fee", None)
        if relay_fee is None:
            # Compatibility path: mempoolminfee is sat/kvB-like.
            relay_fee = max(1, int(self.config.get("mempoolminfee", 1000)) // 1000)
        policy.set_min_relay_fee(max(1, int(relay_fee)))

        limits = MempoolLimits(
            max_size=int(self.config.get("mempool_max_size_bytes", 300_000_000)),
            max_weight=int(self.config.get("mempool_max_weight", 1_500_000_000)),
            max_transactions=int(self.config.get("mempool_max_transactions", 50_000)),
            max_ancestors=int(self.config.get("mempool_max_ancestors", 25)),
            max_descendants=int(self.config.get("mempool_max_descendants", 25)),
            max_ancestor_size_vbytes=int(self.config.get("mempool_max_ancestor_size_vbytes", 101_000)),
            max_descendant_size_vbytes=int(self.config.get("mempool_max_descendant_size_vbytes", 101_000)),
            max_package_count=int(self.config.get("mempool_max_package_count", 25)),
            max_package_weight=int(self.config.get("mempool_max_package_weight", 404_000)),
        )
        self.mempool = Mempool(self.chainstate, policy=policy, limits=limits)
        self.mempool._min_fee_floor_half_life_secs = float(
            self.config.get("mempool_rolling_floor_halflife_secs", 600)
        )
        self.mempool_store = MempoolStore(self.config.get_datadir())
        if bool(self.config.get("persistmempool", True)):
            await self._restore_mempool_from_disk()
        return True

    def _mempool_rules_fingerprint(self) -> str:
        """Compact fingerprint of rule context used for mempool admission."""
        custom_heights: Dict[str, int] = {}
        coinbase_maturity = 100
        if self.chainstate is not None:
            params = getattr(self.chainstate, "params", None)
            custom_heights = dict(getattr(params, "custom_activation_heights", {}) or {})
            coinbase_maturity = int(getattr(params, "coinbase_maturity", 100))
        payload = {
            "network": str(self.config.get("network", "mainnet")),
            "node_consensus_version": int(self.config.get("node_consensus_version", 1)),
            "coinbase_maturity": int(coinbase_maturity),
            "custom_activation_heights": {k: int(v) for k, v in sorted(custom_heights.items())},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _restore_mempool_from_disk(self) -> None:
        """Restore mempool snapshot and revalidate all entries against current chain/rules."""
        if self.mempool is None or self.mempool_store is None or self.chainstate is None:
            return
        snapshot = self.mempool_store.load_snapshot()
        if not snapshot:
            return
        metadata = snapshot.get("metadata", {}) if isinstance(snapshot, dict) else {}
        entries = snapshot.get("entries", []) if isinstance(snapshot, dict) else []
        if not isinstance(entries, list):
            logger.warning("Ignoring mempool snapshot: malformed entries list")
            return

        snap_network = str(metadata.get("network", ""))
        if snap_network and snap_network != self.network:
            logger.warning(
                "Ignoring mempool snapshot from wrong network: snapshot=%s current=%s",
                snap_network,
                self.network,
            )
            return

        tip_hash = str(self.chainstate.get_best_block_hash() or "")
        tip_height = int(self.chainstate.get_best_height())
        if (
            str(metadata.get("tip_hash", "")) != tip_hash
            or int(metadata.get("tip_height", -1)) != tip_height
        ):
            logger.info(
                "Mempool snapshot tip mismatch (snapshot=%s@%s current=%s@%s); full revalidation on restore",
                str(metadata.get("tip_hash", ""))[:16],
                int(metadata.get("tip_height", -1)),
                tip_hash[:16],
                tip_height,
            )

        expected_rules = self._mempool_rules_fingerprint()
        snapshot_rules = str(metadata.get("rules_fingerprint", ""))
        if snapshot_rules and snapshot_rules != expected_rules:
            logger.info("Mempool snapshot rules fingerprint mismatch; strict revalidation enabled")

        restored = 0
        dropped: Dict[str, int] = {}
        for tx_data in sorted(
            entries,
            key=lambda e: (
                int(e.get("height_added", -1)),
                float(e.get("time_added", 0.0)),
                str(e.get("txid", "")),
            ),
        ):
            tx_hex = str(tx_data.get("transaction", "")).strip()
            txid = str(tx_data.get("txid", ""))[:16]
            if not tx_hex:
                dropped["missing_tx_data"] = dropped.get("missing_tx_data", 0) + 1
                continue
            try:
                tx, _ = Transaction.deserialize(bytes.fromhex(tx_hex))
            except Exception:
                dropped["malformed_tx"] = dropped.get("malformed_tx", 0) + 1
                continue
            accepted = await self.mempool.add_transaction(tx, source_peer="mempool_restore")
            if accepted:
                restored += 1
            else:
                reason = self.mempool.last_reject_reason or "restore_rejected"
                dropped[reason] = dropped.get(reason, 0) + 1
                logger.debug("Dropped restored tx %s (%s)", txid, reason)

        logger.info(
            "Mempool restore complete: restored=%s dropped=%s",
            restored,
            dict(sorted(dropped.items())),
        )

    def _flush_mempool_to_disk(self) -> None:
        if (
            self.mempool is None
            or self.mempool_store is None
            or self.chainstate is None
            or not bool(self.config.get("persistmempool", True))
        ):
            return
        ok = self.mempool_store.save(
            dict(self.mempool.transactions),
            network=self.network,
            tip_hash=self.chainstate.get_best_block_hash(),
            tip_height=int(self.chainstate.get_best_height()),
            rules_fingerprint=self._mempool_rules_fingerprint(),
        )
        if not ok:
            logger.warning("Failed to persist mempool snapshot on shutdown")

    async def _init_p2p(self) -> bool:
        """Initialize P2P network."""
        # This release intentionally runs as a full/pruned validating node only.
        if self.mode_manager.is_light_node():
            logger.warning("Light node mode is disabled; initializing full P2P stack")
            self.config.set("lightwallet", False)

        addrman = AddrMan(data_dir=self.config.get_datadir())
        connect_only = self.config.is_connect_only()
        dns_seeds = None
        discovery_sources = self.config.get_peer_discovery_sources()
        if self.network != "regtest" and not self.config.get("allow_missing_bootstrap", False):
            if not any(bool(v) for v in discovery_sources.values()):
                logger.error(
                    "Startup refused: no viable peer discovery source for %s. "
                    "Configure connect/addnode/bootstrap_file/dnsseed or set allow_missing_bootstrap=true.",
                    self.network,
                )
                return False
        if self.config.get("dnsseed"):
            seed_hosts = self.config.get_dns_seed_hosts()
            dns_seeds = DNSSeeds(seeds=list(seed_hosts), network=self.network)
            if not seed_hosts and self.network != "regtest":
                logger.warning("dnsseed enabled but no seed hosts available for %s", self.network)

        logger.info(
            "Peer discovery sources priority: connect=%s addnode=%s bootstrap=%s dns=%s",
            len(discovery_sources.get("connect", [])),
            len(discovery_sources.get("addnode", [])),
            len(discovery_sources.get("bootstrap_file", [])),
            len(discovery_sources.get("dns_seeds", [])),
        )

        self.connman = ConnectionManager(
            addrman=addrman,
            max_connections=self.config.get("maxconnections"),
            max_outbound=self.config.get("maxoutbound"),
            dns_seeds=dns_seeds,
            node_config=self.config,
            connect_only=connect_only,
        )
        self.connman.on_message = self._on_p2p_message
        self.connman.peer_scores.configure_persistence(self.config.get_datadir())
        if self.mempool is not None:
            self.mempool.connman = self.connman

        return True

    async def list_banned(self) -> list:
        if not self.connman:
            return []
        return self.connman.peer_scores.list_banned()

    async def set_ban(
        self,
        address: str,
        action: str = "add",
        bantime: int = 86400,
        reason: str = "manual",
    ) -> Dict[str, object]:
        if not self.connman:
            return {"error": "P2P unavailable"}
        return self.connman.peer_scores.set_ban(
            address=address,
            action=action,
            bantime=int(bantime),
            reason=str(reason or "manual"),
        )

    async def clear_banned(self) -> Dict[str, object]:
        if not self.connman:
            return {"error": "P2P unavailable"}
        return self.connman.peer_scores.clear_banned()

    async def _init_wallet(self) -> bool:
        """Initialize canonical simple private-key wallet manager."""
        if self.config.get("disablewallet", False):
            self.simple_wallet_manager = None
            return True

        self.simple_wallet_manager = SimpleWalletManager(
            self.config.get_datadir(),
            network=self.config.get("network", "mainnet"),
        )
        activate_key = str(self.config.get("wallet_private_key", "") or "").strip()
        if activate_key:
            try:
                wallet = self.simple_wallet_manager.activate_wallet(activate_key)
                logger.info("Activated simple wallet from config: %s", wallet.address[:16])
            except Exception as e:
                logger.error("Failed to activate simple wallet from wallet_private_key: %s", e)
                return False
        return True

    async def _init_mining(self) -> bool:
        """Initialize mining node."""
        is_regtest = self.config.get("network") == "regtest"
        use_miner = self.mode_manager.is_mining() or is_regtest
        if not use_miner:
            self.miner = None
            return True

        mining_address = self.config.get("miningaddress")

        # If mining address not set, derive it from active private-key wallet identity.
        manager = getattr(self, "simple_wallet_manager", None)
        active_wallet = manager.get_active_wallet() if manager else None
        if not mining_address and active_wallet:
            mining_address = active_wallet.address
            self.config.set("miningaddress", mining_address)

        # Ensure required subsystems
        assert self.chainstate is not None
        if self.mempool is None:
            # try to initialize a simple mempool if missing
            self.mempool = Mempool(self.chainstate)

        require_wallet_match = bool(self.config.get("mining_require_wallet_match", True))
        guard = None
        if require_wallet_match:
            guard = lambda addr: (
                bool(addr)
                and bool(getattr(self, "simple_wallet_manager", None))
                and bool(self.simple_wallet_manager.get_active_wallet())
                and self.simple_wallet_manager.get_active_wallet().address == addr
            )

        self.miner = MiningNode(
            self.chainstate,
            self.mempool,
            mining_address or "",
            p2p_manager=self.connman,
            address_guard=guard,
            block_acceptor=self.on_block,
        )
        configured_target_secs = int(self.config.get("mining_target_time_secs", 0) or 0)
        if configured_target_secs > 0:
            self.miner.target_time = configured_target_secs
            logger.info(
                "Mining target time override active: %ss/block",
                configured_target_secs,
            )

        return True

    async def _init_rpc(self) -> bool:
        """Initialize RPC server."""
        rpc_host = self.config.get_rpc_bind()
        rpc_port = int(self.config.get("rpcport", 8332))

        self.rpc_server = RPCServer(
            host=rpc_host,
            port=rpc_port,
            rpc_dir=str(self.config.get_datadir()),
            config=self.config,
        )

        # Register handlers
        control = ControlHandlers(self)
        blockchain = BlockchainHandlers(self)
        mempool = MempoolHandlers(self)
        mining = MiningHandlers(self)
        mining_control = MiningControlHandlers(self)
        wallet = WalletHandlers(self)
        w_ctrl = WalletControlHandlers(self)

        handlers = {
            # Control
            "get_info": control.get_info,
            "stop": control.stop,
            "help": control.help,
            "get_network_info": control.get_network_info,
            "ping": control.ping,
            "uptime": control.uptime,
            "get_health": control.get_health,
            "get_readiness": control.get_readiness,
            "get_metrics": control.get_metrics,
            "listbanned": self.list_banned,
            "setban": self.set_ban,
            "clearbanned": self.clear_banned,

            # Blockchain
            "get_blockchain_info": blockchain.get_blockchain_info,
            "get_block": blockchain.get_block,
            "get_block_count": blockchain.get_block_count,
            "get_best_block_hash": blockchain.get_best_block_hash,

            # Mempool
            "get_mempool_info": mempool.get_mempool_info,
            "get_mempool_diagnostics": mempool.get_mempool_diagnostics,
            "get_raw_mempool": mempool.get_raw_mempool,
            "send_raw_transaction": mempool.send_raw_transaction,
            "submit_package": mempool.submit_package,

            # Wallet (private-key model)
            "get_wallet_info": wallet.get_wallet_info,
            "get_balance": wallet.get_balance,
            "get_new_address": wallet.get_new_address,
            "send_to_address": wallet.send_to_address,
            "importxpubwatchonly": wallet.import_xpub_watchonly,
            "walletcreatefundedpsbt": wallet.wallet_create_funded_psbt,
            "walletprocesspsbt": wallet.wallet_process_psbt,
            "finalizepsbt": wallet.finalize_psbt,
            "createmultisigpolicy": wallet.create_multisig_policy,
            "createwallet": w_ctrl.create_wallet,
            "loadwallet": w_ctrl.load_wallet,
            "listwallets": w_ctrl.list_wallets,
            "activatewallet": w_ctrl.activate_wallet,
            "walletpassphrase": w_ctrl.wallet_passphrase,
            "walletlock": w_ctrl.wallet_lock,

            # Mining
            "get_mining_info": mining.get_mining_info,
            "get_block_template": mining.get_block_template,
            "submit_block": mining.submit_block,
            "generate": mining.generate,
            "setgenerate": mining_control.set_generate,
            "getminingstatus": mining_control.get_mining_status,
            "setminingaddress": mining_control.set_mining_address,
        }
        self.rpc_server.register_handlers(handlers)
        self.rpc_server.register_status_providers(
            health_provider=control.get_health,
            readiness_provider=control.get_readiness,
            metrics_provider=control.get_metrics,
            prometheus_provider=(
                lambda: (
                    self.metrics_collector.to_prometheus()
                    if self.metrics_collector is not None
                    else "# metrics unavailable\n"
                )
            ),
        )

        # Web dashboard
        if self.config.get("webdashboard", False):
            from node.web.mining_wallet_dashboard import MiningWalletDashboard
            self.dashboard = MiningWalletDashboard(
                self,
                str(self.config.get("webhost", "127.0.0.1")),
                int(self.config.get("webport", 8080)),
            )
            await self.dashboard.start()
            host = self.config.get('webhost')
            port = self.config.get('webport')
            logger.info(
                "Web dashboard on http://%s:%s",
                host, port,
            )

        return True

    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def request_stop():
            stop_event.set()

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

        await stop_event.wait()
        await self.stop()


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="BerzCoin Node")
    parser.add_argument("-conf", help="Configuration file")
    parser.add_argument("-datadir", help="Data directory")
    parser.add_argument("--testnet", action="store_true", help="Use testnet")
    parser.add_argument("--regtest", action="store_true", help="Use regtest")
    parser.add_argument(
        "--wallet-private-key",
        help="Activate wallet with private key",
    )
    parser.add_argument(
        "--activation-height",
        action="append",
        default=[],
        metavar="NAME=HEIGHT",
        help="Override consensus activation height (repeatable)",
    )

    args = parser.parse_args()

    node = BerzCoinNode(args.conf)

    if args.datadir:
        node.config.set("datadir", args.datadir)
    if args.regtest:
        node.config.set("network", "regtest")
    elif args.testnet:
        node.config.set("network", "testnet")
    if args.wallet_private_key:
        node.config.set("wallet_private_key", args.wallet_private_key)
    if args.activation_height:
        try:
            cli_overrides = Config.parse_activation_height_items(args.activation_height)
        except ValueError as e:
            parser.error(str(e))
        merged = Config.parse_activation_height_items(
            node.config.get("custom_activation_heights", {})
        )
        merged.update(cli_overrides)
        node.config.set("custom_activation_heights", merged)

    if not await node.initialize():
        sys.exit(1)

    await node.start()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
