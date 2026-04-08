"""Connection manager for P2P network."""

import asyncio
import random
import ipaddress
import time
from typing import Any, List, Dict, Set, Optional, Callable
from pathlib import Path
from shared.utils.logging import get_logger
from .peer import Peer
from .addrman import AddrMan
from .dns_seeds import DNSSeeds
from .authority import NodeAuthorityChain
from .peer_scoring import PeerScoringManager
from .limits import OutboundClass, OutboundPolicy

logger = get_logger()

class ConnectionManager:
    """Manages peer connections."""

    def __init__(
        self,
        addrman: AddrMan,
        max_connections: int = 125,
        max_outbound: int = 8,
        dns_seeds: Optional[DNSSeeds] = None,
        node_config: Optional[Any] = None,
        connect_only: bool = False,
    ):
        self.addrman = addrman
        self.max_connections = max_connections
        self.max_outbound = max_outbound
        self.dns_seeds = dns_seeds
        self.node_config = node_config
        self.connect_only = connect_only
        self.peers: Dict[str, Peer] = {}
        self.outbound_peers: Dict[str, Peer] = {}
        self.inbound_peers: Dict[str, Peer] = {}
        self.on_peer_connected: Optional[Callable] = None
        self.on_peer_disconnected: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self._running = False
        self._connect_task: Optional[asyncio.Task] = None
        self._discover_task: Optional[asyncio.Task] = None
        self._peer_discover_interval_secs = 300
        self._server: Optional[asyncio.base_events.Server] = None
        self.max_inbound_per_ip = 8
        self.max_inbound_per_netgroup = 16
        self.max_outbound_per_netgroup = 2
        self.min_outbound_netgroups = 4
        self.network_hardening = bool(
            self.node_config.get("network_hardening", False)
        ) if self.node_config else False
        self.peer_scores = PeerScoringManager(
            network_hardening=self.network_hardening
        )
        self.outbound_classes: Dict[str, str] = {}
        self._pending_outbound_class: Dict[str, str] = {}
        self.outbound_policy = OutboundPolicy()
        if self.network_hardening:
            self.min_outbound_netgroups = max(
                self.min_outbound_netgroups, self.outbound_policy.min_anchor_netgroups
            )
        self.authority_chain_enabled = bool(
            self.node_config.get("authority_chain_enabled", False)
        ) if self.node_config else False
        trusted = self.node_config.get("authority_trusted_nodes", []) if self.node_config else []
        self.authority_chain = NodeAuthorityChain(trusted_nodes=trusted)
        self._last_getaddr_at: Dict[str, float] = {}
        self._getaddr_interval_secs = 180
        self._last_feeler_at = 0.0
        self._last_rotation_at = 0.0

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
            # Treat plain IPv6 literal without explicit port as host-only.
            return raw, int(default_port)
        if ":" in raw:
            host, port = raw.rsplit(":", 1)
            try:
                return host, int(port)
            except ValueError:
                return host, int(default_port)
        return raw, int(default_port)

    def _default_p2p_port(self) -> int:
        if self.node_config:
            return int(self.node_config.get("port", 8333))
        return 8333

    def _load_peers_from_config(self) -> None:
        if not self.node_config:
            return
        cfg = self.node_config
        connect = cfg.get_connect_peers()
        if connect:
            # Strict priority: when connect=... is set, it is the only discovery source.
            for addr in connect:
                self.addrman.add_static_peer(addr, priority=0)
            logger.info("Loaded %s connect peer(s) (connect-only discovery)", len(connect))
            return

        addnodes = cfg.get_addnode_peers()
        for addr in addnodes:
            self.addrman.add_static_peer(addr, priority=10)

        if cfg.get("bootstrap_enabled", True):
            nodes = cfg.get_bootstrap_nodes()
            if nodes:
                self.addrman.add_bootstrap_nodes(nodes, priority=20)

        if addnodes:
            logger.info("Loaded %s addnode peer(s)", len(addnodes))

    async def _load_bootstrap_nodes(self):
        """Load bootstrap nodes from config file."""
        # Determine datadir path
        if self.node_config and hasattr(self.node_config, 'get_datadir'):
            datadir = Path(self.node_config.get_datadir())
        else:
            datadir = Path.cwd()

        bootstrap_path = datadir / "bootstrap_nodes.json"

        if not bootstrap_path.exists():
            # Use hardcoded seeds from config if available
            seeds = []
            try:
                if self.node_config:
                    seeds = self.node_config.get('hardcoded_seeds', [])
            except Exception:
                seeds = []
            for seed in seeds:
                self.addrman.add_static_peer(seed)
            return

        try:
            import json
            with open(bootstrap_path, 'r') as f:
                data = json.load(f)

            # Add hardcoded seeds
            for seed in data.get('hardcoded_seeds', []):
                self.addrman.add_static_peer(seed)

            # Add DNS seeds
            for seed in data.get('bootstrap_nodes', []):
                if 'address' in seed:
                    # Resolve DNS seed
                    await self._resolve_dns_seed(seed['address'], seed.get('port', self._default_p2p_port()))

            logger.info(f"Loaded {len(self.addrman.get_static_peers())} bootstrap nodes")
        except Exception as e:
            logger.error(f"Failed to load bootstrap nodes: {e}")

    async def _resolve_dns_seed(self, hostname, port):
        """Resolve DNS seed to IP addresses."""
        try:
            addrs = await asyncio.get_event_loop().getaddrinfo(hostname, port)
            for addr in addrs:
                ip = addr[4][0]
                self.addrman.add_static_peer(f"{ip}:{port}")
        except Exception as e:
            logger.debug(f"Failed to resolve {hostname}: {e}")

    async def start(self) -> None:
        self._running = True
        self._load_peers_from_config()
        if self.network_hardening:
            for anchor in self.addrman.get_anchor_peers():
                self.addrman.add(anchor)
        # Start inbound listener unless in connect-only mode.
        if not self.connect_only:
            try:
                bind = (
                    str(self.node_config.get("bind", "0.0.0.0"))
                    if self.node_config
                    else "0.0.0.0"
                )
                port = self.node_config.get("port", 8333) if self.node_config else 8333
                host = bind
                self._server = await asyncio.start_server(self.accept_connection, host, int(port))
                logger.info(f"P2P listener started on {host}:{port}")
            except Exception as e:
                logger.error(f"Failed to start P2P listener: {e}")

        self._connect_task = asyncio.create_task(self._maintain_connections())
        self._discover_task = asyncio.create_task(self._discover_loop())
        logger.info("Connection manager started")

    async def stop(self) -> None:
        self._running = False
        if self._connect_task:
            self._connect_task.cancel()
        if self._discover_task:
            self._discover_task.cancel()
        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
        for peer in list(self.peers.values()):
            await peer.disconnect()
        self.peers.clear()
        self.outbound_peers.clear()
        self.inbound_peers.clear()
        logger.info("Connection manager stopped")

    async def _discover_loop(self) -> None:
        while self._running:
            try:
                await self._discover_peers()
                await asyncio.sleep(self._peer_discover_interval_secs)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Peer discovery error: %s", e)
                await asyncio.sleep(60)

    async def _add_from_seed(self, seed_host: str) -> int:
        """Resolve a DNS seed hostname and register peers in AddrMan (non-blocking)."""
        return await asyncio.to_thread(
            self.addrman.add_from_dns_seed, seed_host, self._default_p2p_port()
        )

    async def _discover_peers(self) -> None:
        """Discover peers from DNS seeds and request addr relays from connected peers."""
        if self.dns_seeds:
            for seed in self.dns_seeds.seeds:
                try:
                    added = await self._add_from_seed(seed)
                    if added:
                        logger.debug("DNS seed %s added %s new addr(s) to AddrMan", seed, added)
                except Exception as e:
                    logger.warning("DNS seed %s: %s", seed, e)

        if self.connect_only:
            return

        # Query a random subset for address relay to reduce amplification/spam surface.
        candidates = [p for p in self.peers.values() if p.connected]
        random.shuffle(candidates)
        seen_groups: Set[str] = set()
        selected: List[Peer] = []
        for peer in candidates:
            group = self._netgroup_for_address(peer.address)
            if group in seen_groups:
                continue
            seen_groups.add(group)
            selected.append(peer)
            if len(selected) >= 2:
                break

        for peer in selected:
            if peer.connected:
                now = asyncio.get_event_loop().time()
                last = self._last_getaddr_at.get(peer.address, 0.0)
                if now - last < self._getaddr_interval_secs:
                    continue
                await peer.send_getaddr()
                self._last_getaddr_at[peer.address] = now

    async def _maintain_connections(self) -> None:
        while self._running:
            try:
                needed = self.max_outbound - len(self.outbound_peers)
                if needed > 0:
                    await self._connect_outbound(needed)
                await self._evict_bad_outbound()
                if self.network_hardening:
                    await self._maybe_run_feeler()
                    await self._maybe_rotate_outbound()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error maintaining connections: {e}")
                await asyncio.sleep(30)

    async def _connect_outbound(self, count: int) -> None:
        connected = 0
        netgroup_counts = self._outbound_netgroup_counts()
        class_plan = (
            self._desired_outbound_classes()
            if self.network_hardening
            else [OutboundClass.FULL_RELAY] * max(0, int(count))
        )
        for outbound_class in class_plan[:count]:
            addresses = self._candidate_addresses_for_class(outbound_class, count)
            for addr in addresses:
                if connected >= count:
                    break
                if addr in self.peers:
                    continue
                netgroup = self._netgroup_for_address(addr)
                if netgroup and netgroup_counts.get(netgroup, 0) >= self.max_outbound_per_netgroup:
                    continue
                score = self.peer_scores.get_score(addr)
                if not score.should_connect():
                    continue
                if await self._connect_outbound_address(addr, outbound_class):
                    if netgroup:
                        netgroup_counts[netgroup] = netgroup_counts.get(netgroup, 0) + 1
                    connected += 1
                    break
            if connected >= count:
                break

    def _candidate_addresses_for_class(self, outbound_class: str, count: int) -> List[str]:
        if not self.network_hardening:
            addresses = self.addrman.get_addresses(max(1, count * 2))
            if self.dns_seeds and len(addresses) < count:
                # best-effort non-blocking fallback list; discovery loop resolves in background
                addresses.extend([s for s in self.dns_seeds.cache if s not in addresses])
            random.shuffle(addresses)
            return addresses

        candidates: List[str] = []
        if outbound_class == OutboundClass.ANCHOR:
            candidates.extend(sorted(self.addrman.get_anchor_peers()))
        candidates.extend(self.addrman.get_addresses(max(4, count * 3)))
        # Deterministic ordering to make rotation behavior reproducible.
        seen: Set[str] = set()
        ordered: List[str] = []
        for addr in candidates:
            if addr in seen:
                continue
            seen.add(addr)
            ordered.append(addr)
        ordered.sort(key=lambda a: (-self.peer_scores.get_score(a).score, a))
        return ordered

    async def _connect_outbound_address(self, addr: str, outbound_class: str) -> bool:
        default_port = self._default_p2p_port()
        host, port = self._split_host_port(addr, default_port)
        if not host:
            return False
        peer = Peer(host, int(port), is_outbound=True)
        peer.on_message = self.on_message
        peer.on_disconnect = self._on_peer_disconnect
        self._pending_outbound_class[peer.address] = outbound_class
        if await peer.connect():
            self._add_peer(peer)
            self.peer_scores.record_good(peer.address)
            self.addrman.mark_good(peer.address)
            if self.authority_chain_enabled:
                self.authority_chain.verify_from_local(peer.address)
            if self.network_hardening and outbound_class == OutboundClass.ANCHOR:
                self.addrman.add_anchor_peer(peer.address)
            logger.info("Connected to %s (%s)", peer.address, outbound_class)
            return True
        self._pending_outbound_class.pop(peer.address, None)
        self.peer_scores.record_bad(addr, "connect_failed")
        self.addrman.mark_failed(addr)
        return False

    def _outbound_class_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {
            OutboundClass.ANCHOR: 0,
            OutboundClass.FULL_RELAY: 0,
            OutboundClass.BLOCK_RELAY_ONLY: 0,
            OutboundClass.FEELER: 0,
        }
        for klass in self.outbound_classes.values():
            counts[klass] = counts.get(klass, 0) + 1
        return counts

    def _desired_outbound_classes(self) -> List[str]:
        counts = self._outbound_class_counts()
        remaining = max(0, self.max_outbound - len(self.outbound_peers))
        plan: List[str] = []
        while remaining > 0 and counts.get(OutboundClass.ANCHOR, 0) < self.outbound_policy.target_anchor_peers:
            plan.append(OutboundClass.ANCHOR)
            counts[OutboundClass.ANCHOR] += 1
            remaining -= 1
        while (
            remaining > 0
            and counts.get(OutboundClass.BLOCK_RELAY_ONLY, 0)
            < self.outbound_policy.target_block_relay_only_peers
        ):
            plan.append(OutboundClass.BLOCK_RELAY_ONLY)
            counts[OutboundClass.BLOCK_RELAY_ONLY] += 1
            remaining -= 1
        plan.extend([OutboundClass.FULL_RELAY] * remaining)
        return plan

    def _add_peer(self, peer: Peer) -> None:
        self.peers[peer.address] = peer
        if peer.is_outbound:
            self.outbound_peers[peer.address] = peer
            klass = self._pending_outbound_class.pop(
                peer.address,
                OutboundClass.FULL_RELAY,
            )
            self.outbound_classes[peer.address] = klass
        else:
            self.inbound_peers[peer.address] = peer
        if self.on_peer_connected:
            self.on_peer_connected(peer)

    async def _on_peer_disconnect(self, peer: Peer) -> None:
        self.peers.pop(peer.address, None)
        self.outbound_peers.pop(peer.address, None)
        self.inbound_peers.pop(peer.address, None)
        self.outbound_classes.pop(peer.address, None)
        self._pending_outbound_class.pop(peer.address, None)
        if self.on_peer_disconnected:
            await self.on_peer_disconnected(peer)
        logger.info(f"Peer disconnected: {peer.address}")

    async def accept_connection(self, reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter) -> None:
        if len(self.peers) >= self.max_connections:
            peername = writer.get_extra_info('peername')
            inbound_addr = ""
            if peername and len(peername) >= 2:
                inbound_addr = f"{peername[0]}:{int(peername[1])}"
            if not await self._evict_worst_inbound_for(inbound_addr):
                writer.close()
                await writer.wait_closed()
                return
        peername = writer.get_extra_info('peername')
        if not peername or len(peername) < 2:
            writer.close()
            await writer.wait_closed()
            return
        host, port = peername[0], int(peername[1])
        inbound_addr = f"{host}:{port}"
        if self.authority_chain_enabled:
            if not self.authority_chain.can_accept(inbound_addr, self.peers.keys()):
                logger.warning("Rejecting inbound %s: no trusted verifier available", inbound_addr)
                writer.close()
                await writer.wait_closed()
                return
        if not self.peer_scores.get_score(inbound_addr).should_connect():
            writer.close()
            await writer.wait_closed()
            return
        inbound_from_ip = sum(1 for p in self.inbound_peers.values() if p.host == host)
        if inbound_from_ip >= self.max_inbound_per_ip:
            logger.warning("Rejecting inbound from %s: per-IP limit reached", host)
            writer.close()
            await writer.wait_closed()
            return
        inbound_netgroup = self._netgroup_for_address(inbound_addr)
        if inbound_netgroup:
            inbound_same_group = sum(
                1 for p in self.inbound_peers.values()
                if self._netgroup_for_address(p.address) == inbound_netgroup
            )
            if inbound_same_group >= self.max_inbound_per_netgroup:
                logger.warning("Rejecting inbound from %s: netgroup limit reached", inbound_addr)
                writer.close()
                await writer.wait_closed()
                return
        peer_addr = f"{host}:{port}"
        if peer_addr in self.peers:
            writer.close()
            return
        peer = Peer(host, port, is_outbound=False)
        peer.reader = reader
        peer.writer = writer
        peer.on_message = self.on_message
        peer.on_disconnect = self._on_peer_disconnect
        if not await peer._handshake():
            self.peer_scores.record_bad(peer.address, "handshake_failed")
            await peer.disconnect()
            return
        peer.connected = True
        peer.connected_at = asyncio.get_event_loop().time()
        self._add_peer(peer)
        self.peer_scores.record_good(peer.address)
        if self.authority_chain_enabled:
            verifier = self.authority_chain.verify_with_connected_verifier(
                peer.address,
                self.peers.keys(),
            )
            if verifier is None:
                # Fallback to local verification for admitted inbound nodes.
                self.authority_chain.verify_from_local(peer.address)
                verifier = "local"
            logger.info("Authority-chain verified inbound node %s via %s", peer.address, verifier)
        asyncio.create_task(peer._handle_messages())
        logger.info(f"Inbound connection from {peer.address}")

    async def _evict_bad_outbound(self) -> None:
        if not self.outbound_peers:
            return
        worst = self._select_outbound_eviction_candidate()
        if not worst:
            return
        try:
            await worst.disconnect()
            self.peer_scores.record_bad(worst.address, "stale_peer")
        except Exception:
            pass

    def _protected_outbound_addresses(self) -> Set[str]:
        """Protect at least one high-quality outbound peer per netgroup."""
        grouped: Dict[str, List[Peer]] = {}
        for peer in self.outbound_peers.values():
            group = self._netgroup_for_address(peer.address)
            grouped.setdefault(group, []).append(peer)

        protected: Set[str] = set()
        for peers in grouped.values():
            best = max(
                peers,
                key=lambda p: (
                    self.peer_scores.get_score(p.address).score,
                    getattr(p, "connected_at", 0.0),
                ),
            )
            protected.add(best.address)

        # Keep some long-lived anchors even when many peers share netgroups.
        if len(protected) < self.min_outbound_netgroups:
            by_uptime = sorted(
                self.outbound_peers.values(),
                key=lambda p: getattr(p, "connected_at", 0.0),
            )
            for peer in by_uptime:
                protected.add(peer.address)
                if len(protected) >= self.min_outbound_netgroups:
                    break
        if self.network_hardening:
            anchor_peers = [
                p for p in self.outbound_peers.values()
                if self.outbound_classes.get(p.address) == OutboundClass.ANCHOR
            ]
            anchor_groups: Dict[str, List[Peer]] = {}
            for peer in anchor_peers:
                grp = self._netgroup_for_address(peer.address)
                anchor_groups.setdefault(grp, []).append(peer)
            kept_groups = 0
            for grp in sorted(anchor_groups.keys()):
                best = max(
                    anchor_groups[grp],
                    key=lambda p: (
                        self.peer_scores.get_score(p.address).score,
                        getattr(p, "connected_at", 0.0),
                    ),
                )
                protected.add(best.address)
                kept_groups += 1
                if kept_groups >= self.outbound_policy.min_anchor_netgroups:
                    break
        return protected

    def _select_outbound_eviction_candidate(self) -> Optional[Peer]:
        candidates = [
            p for p in self.outbound_peers.values() if self.peer_scores.should_evict(p.address)
        ]
        if not candidates:
            return None
        protected = self._protected_outbound_addresses()
        unprotected = [p for p in candidates if p.address not in protected]
        pool = unprotected if unprotected else candidates
        return min(
            pool,
            key=lambda p: (
                self.peer_scores.get_score(p.address).score,
                getattr(p, "connected_at", 0.0),
            ),
        )

    def filter_and_add_addrs(self, source_peer: Optional[str], addrs: List[str]) -> int:
        """Validate/limit relayed addrs before adding to AddrMan."""
        added = 0
        unique: Set[str] = set()
        max_accept = 1000
        per_netgroup_cap = 64
        netgroup_seen: Dict[str, int] = {}
        for addr in addrs[:max_accept]:
            if addr in unique:
                continue
            unique.add(addr)
            host, _port = self._split_host_port(addr, self._default_p2p_port())
            if not host:
                continue
            try:
                ip = ipaddress.ip_address(host)
                if not ip.is_global:
                    continue
            except ValueError:
                # Ignore malformed/non-IP relay addresses in this hardened mode.
                continue
            netgroup = self._netgroup_for_address(addr)
            if netgroup_seen.get(netgroup, 0) >= per_netgroup_cap:
                continue
            netgroup_seen[netgroup] = netgroup_seen.get(netgroup, 0) + 1
            if self.peer_scores.get_score(addr).is_banned():
                continue
            if self.addrman.add(addr):
                added += 1
        if source_peer and len(addrs) > max_accept:
            self.peer_scores.record_bad(source_peer, "addr_spam")
        return added

    def _netgroup_for_address(self, address: str) -> str:
        host, _port = self._split_host_port(address, self._default_p2p_port())
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return host
        if isinstance(ip, ipaddress.IPv4Address):
            parts = host.split(".")
            return ".".join(parts[:2])
        return str(ipaddress.IPv6Address(int(ip) >> 64 << 64))

    def _outbound_netgroup_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for peer in self.outbound_peers.values():
            group = self._netgroup_for_address(peer.address)
            if not group:
                continue
            counts[group] = counts.get(group, 0) + 1
        return counts

    async def _evict_worst_inbound_for(self, new_addr: str) -> bool:
        if not self.inbound_peers:
            return False
        new_score = self.peer_scores.get_score(new_addr).score if new_addr else 0
        inbound_group_counts: Dict[str, int] = {}
        for peer in self.inbound_peers.values():
            group = self._netgroup_for_address(peer.address)
            inbound_group_counts[group] = inbound_group_counts.get(group, 0) + 1
        new_group = self._netgroup_for_address(new_addr) if new_addr else ""
        is_new_group = bool(new_group) and new_group not in inbound_group_counts

        candidates = list(self.inbound_peers.values())
        redundant = [
            p
            for p in candidates
            if inbound_group_counts.get(self._netgroup_for_address(p.address), 0) > 1
        ]
        pool = redundant if redundant else candidates
        worst = min(
            pool,
            key=lambda p: (
                -inbound_group_counts.get(self._netgroup_for_address(p.address), 0),
                self.peer_scores.get_score(p.address).score,
                getattr(p, "connected_at", 0.0),
            ),
        )
        worst_score = self.peer_scores.get_score(worst.address).score
        # Prefer diversity improvements: a newcomer from a new netgroup can replace
        # a redundant incumbent even if scores are similar.
        if not is_new_group and new_score <= worst_score and worst_score >= 0:
            return False
        try:
            await worst.disconnect()
            self.peer_scores.record_bad(worst.address, "evicted_for_inbound_slot")
            return True
        except Exception:
            return False

    async def _maybe_run_feeler(self) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._last_feeler_at < float(self.outbound_policy.feeler_interval_secs):
            return
        self._last_feeler_at = now
        candidate = self._select_feeler_candidate()
        if not candidate:
            return
        await self._run_feeler(candidate)

    def _select_feeler_candidate(self) -> Optional[str]:
        candidates = self.addrman.get_addresses(32)
        connected = set(self.peers.keys())
        filtered = [c for c in candidates if c not in connected]
        if not filtered:
            return None
        filtered.sort(
            key=lambda a: (
                self.peer_scores.get_score(a).failures,
                -self.peer_scores.get_score(a).score,
                a,
            )
        )
        return filtered[0]

    async def _run_feeler(self, address: str) -> None:
        default_port = self._default_p2p_port()
        host, port = self._split_host_port(address, default_port)
        if not host:
            return
        peer = Peer(host, int(port), is_outbound=True)
        ok = await peer.connect()
        if ok:
            self.addrman.mark_good(peer.address)
            self.peer_scores.record_good(peer.address)
            await peer.disconnect()
        else:
            self.addrman.mark_failed(address)
            self.peer_scores.record_bad(address, "connect_failed")

    async def _maybe_rotate_outbound(self) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._last_rotation_at < float(self.outbound_policy.rotation_interval_secs):
            return
        self._last_rotation_at = now
        if len(self.outbound_peers) < max(2, self.max_outbound):
            return
        candidate = self._select_rotation_candidate()
        if candidate is None:
            return
        await candidate.disconnect()
        self.peer_scores.record_bad(candidate.address, "stale_peer")

    def _select_rotation_candidate(self) -> Optional[Peer]:
        protected = self._protected_outbound_addresses()
        candidates = [
            p for p in self.outbound_peers.values()
            if p.address not in protected
        ]
        if not candidates:
            return None
        net_counts = self._outbound_netgroup_counts()
        now = asyncio.get_event_loop().time()
        return min(
            candidates,
            key=lambda p: (
                self.peer_scores.get_score(p.address).score,
                -(now - float(getattr(p, "connected_at", now))),
                -net_counts.get(self._netgroup_for_address(p.address), 0),
                p.address,
            ),
        )

    async def broadcast(self, command: str, payload: bytes, exclude: Optional[Set[str]] = None) -> None:
        exclude = exclude or set()
        for peer in self.peers.values():
            if peer.address not in exclude and peer.connected:
                await peer.send_message(command, payload)

    async def broadcast_block(self, block) -> None:
        """Broadcast mined block to all peers."""
        from shared.protocol.messages import InvMessage, CmpctBlockMessage

        block_hash = block.header.hash()
        inv = InvMessage(inventory=[(InvMessage.InvType.MSG_BLOCK, block_hash)])
        cmpct_payload: Optional[bytes] = None

        # Broadcast to all peers
        for peer in self.peers.values():
            if not peer.connected:
                continue
            if getattr(peer, "prefers_compact_blocks", False):
                if cmpct_payload is None:
                    cmpct_payload = CmpctBlockMessage.from_block(
                        block, nonce=int(time.time())
                    ).serialize()
                await peer.send_message("cmpctblock", cmpct_payload)
            else:
                await peer.send_message("inv", inv.serialize())

        logger.info(f"Block broadcast to {len(self.peers)} peers")

    async def send_to_random(self, command: str, payload: bytes, count: int = 1) -> None:
        if not self.peers:
            return
        peers = list(self.peers.values())
        random.shuffle(peers)
        for peer in peers[:count]:
            await peer.send_message(command, payload)

    def get_peer(self, address: str) -> Optional[Peer]:
        return self.peers.get(address)

    def get_outbound_peers(self) -> List[Peer]:
        return list(self.outbound_peers.values())

    def get_inbound_peers(self) -> List[Peer]:
        return list(self.inbound_peers.values())

    def get_connected_count(self) -> int:
        return len(self.peers)

    def get_outbound_count(self) -> int:
        return len(self.outbound_peers)

    def get_inbound_count(self) -> int:
        return len(self.inbound_peers)

    def get_best_height_peer(self) -> Optional[Peer]:
        best_peer = None
        best_height = -1
        for peer in self.peers.values():
            if peer.peer_height > best_height:
                best_height = peer.peer_height
                best_peer = peer
        return best_peer

    def get_synced_peers(self, current_height: int, tolerance: int = 10) -> List[Peer]:
        return [p for p in self.peers.values() if abs(p.peer_height - current_height) <= tolerance]

    def get_stats(self) -> Dict[str, int]:
        stats = {
            'total': self.get_connected_count(),
            'outbound': self.get_outbound_count(),
            'inbound': self.get_inbound_count(),
            'max': self.max_connections,
            'max_outbound': self.max_outbound,
        }
        if self.network_hardening:
            stats.update(
                {
                    "anchors": self._outbound_class_counts().get(OutboundClass.ANCHOR, 0),
                    "block_relay_only": self._outbound_class_counts().get(
                        OutboundClass.BLOCK_RELAY_ONLY, 0
                    ),
                    "full_relay": self._outbound_class_counts().get(
                        OutboundClass.FULL_RELAY, 0
                    ),
                }
            )
        return stats
