"""Connection manager for P2P network."""

import asyncio
import random
from typing import Any, List, Dict, Set, Optional, Callable
from pathlib import Path
from shared.utils.logging import get_logger
from .peer import Peer
from .addrman import AddrMan
from .dns_seeds import DNSSeeds

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

    def _default_p2p_port(self) -> int:
        if self.node_config:
            return int(self.node_config.get("port", 8333))
        return 8333

    def _load_peers_from_config(self) -> None:
        if not self.node_config:
            return
        cfg = self.node_config
        if cfg.get("bootstrap_enabled", True):
            nodes = cfg.get_bootstrap_nodes()
            if nodes:
                self.addrman.add_bootstrap_nodes(nodes)
        for addr in cfg.get_addnode_peers():
            self.addrman.add_static_peer(addr)
        for addr in cfg.get_connect_peers():
            self.addrman.add_static_peer(addr)

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
        # Start inbound listener unless in connect-only mode.
        if not self.connect_only:
            try:
                bind = "0.0.0.0"
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

        for peer in list(self.peers.values()):
            if peer.connected:
                await peer.send_getaddr()

    async def _maintain_connections(self) -> None:
        while self._running:
            try:
                needed = self.max_outbound - len(self.outbound_peers)
                if needed > 0:
                    await self._connect_outbound(needed)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error maintaining connections: {e}")
                await asyncio.sleep(30)

    async def _connect_outbound(self, count: int) -> None:
        addresses = self.addrman.get_addresses(count * 2)
        if len(addresses) < count and self.dns_seeds:
            seeds = await self.dns_seeds.get_seeds()
            for seed in seeds:
                if seed not in self.peers:
                    addresses.append(seed)
        random.shuffle(addresses)
        connected = 0
        for addr in addresses:
            if connected >= count:
                break
            if addr in self.peers:
                continue
            default_port = self._default_p2p_port()
            host, port = addr.split(":") if ":" in addr else (addr, default_port)
            peer = Peer(host, int(port), is_outbound=True)
            peer.on_message = self.on_message
            peer.on_disconnect = self._on_peer_disconnect
            if await peer.connect():
                self._add_peer(peer)
                connected += 1
                logger.info(f"Connected to {peer.address}")

    def _add_peer(self, peer: Peer) -> None:
        self.peers[peer.address] = peer
        if peer.is_outbound:
            self.outbound_peers[peer.address] = peer
        else:
            self.inbound_peers[peer.address] = peer
        if self.on_peer_connected:
            self.on_peer_connected(peer)

    async def _on_peer_disconnect(self, peer: Peer) -> None:
        self.peers.pop(peer.address, None)
        self.outbound_peers.pop(peer.address, None)
        self.inbound_peers.pop(peer.address, None)
        if self.on_peer_disconnected:
            await self.on_peer_disconnected(peer)
        logger.info(f"Peer disconnected: {peer.address}")

    async def accept_connection(self, reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter) -> None:
        if len(self.peers) >= self.max_connections:
            writer.close()
            await writer.wait_closed()
            return
        host, port = writer.get_extra_info('peername')
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
            await peer.disconnect()
            return
        peer.connected = True
        self._add_peer(peer)
        asyncio.create_task(peer._handle_messages())
        logger.info(f"Inbound connection from {peer.address}")

    async def broadcast(self, command: str, payload: bytes, exclude: Optional[Set[str]] = None) -> None:
        exclude = exclude or set()
        for peer in self.peers.values():
            if peer.address not in exclude and peer.connected:
                await peer.send_message(command, payload)

    async def broadcast_block(self, block) -> None:
        """Broadcast mined block to all peers."""
        from shared.protocol.messages import InvMessage

        block_hash = block.header.hash()
        inv = InvMessage(inventory=[(InvMessage.InvType.MSG_BLOCK, block_hash)])

        # Broadcast to all peers
        for peer in self.peers.values():
            if peer.connected:
                await peer.send_message('inv', inv.serialize())

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
        return {
            'total': self.get_connected_count(),
            'outbound': self.get_outbound_count(),
            'inbound': self.get_inbound_count(),
            'max': self.max_connections,
            'max_outbound': self.max_outbound,
        }
