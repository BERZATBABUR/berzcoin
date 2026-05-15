"""Control CLI commands."""

import argparse
from typing import Any, Optional


class ControlCommands:
    """Control CLI commands."""

    def __init__(self, handler: Any):
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser('getinfo', help='Get node info')
        p.set_defaults(command='getinfo')

        p = subparsers.add_parser('stop', help='Stop node')
        p.set_defaults(command='stop')

        p = subparsers.add_parser('nodehelp', help='RPC help (named nodehelp to avoid argparse help)')
        p.add_argument('rpccommand', nargs='?', help='Command name')
        p.set_defaults(command='nodehelp')

        p = subparsers.add_parser('ping', help='Ping node')
        p.set_defaults(command='ping')

        p = subparsers.add_parser('uptime', help='Get node uptime')
        p.set_defaults(command='uptime')

        p = subparsers.add_parser('getnetworkinfo', help='Get network info')
        p.set_defaults(command='getnetworkinfo')

        p = subparsers.add_parser('addpeer', help='Add peer address to node discovery')
        p.add_argument('address', help='Peer address in host:port')
        p.add_argument('--mode', choices=['addnode', 'connect'], default='addnode')
        p.set_defaults(command='addpeer')

        p = subparsers.add_parser('addnode', help='Add static peer (Bitcoin-style alias)')
        p.add_argument('address', help='Peer address in host:port')
        p.set_defaults(command='addnode')

        p = subparsers.add_parser('quickjoin', help='One-step join to starter node (connect + status)')
        p.add_argument('address', help='Starter node address in host:port')
        p.set_defaults(command='quickjoin')

        p = subparsers.add_parser('listpeers', help='List connected/static peers')
        p.add_argument('--verbose', action='store_true', help='Include detailed peer rows')
        p.set_defaults(command='listpeers')

        p = subparsers.add_parser('verifypeer', help='Add authority-chain attestation for a candidate peer')
        p.add_argument('target', help='Candidate peer host:port or node id')
        p.add_argument('--verifier-id', default='', help='Verifier identity (for example pubkey:...)')
        p.add_argument('--verifier-node', default='local', help='Verifier node id/address')
        p.set_defaults(command='verifypeer')

        p = subparsers.add_parser('verify-node', help='Verify candidate node (trusted verifier alias)')
        p.add_argument('target', help='Candidate peer host:port or node id')
        p.add_argument('--verifier-id', default='', help='Verifier identity (for example pubkey:...)')
        p.add_argument('--verifier-node', default='local', help='Verifier node id/address')
        p.set_defaults(command='verify-node')

        p = subparsers.add_parser('joinnetwork', help='Register + discover peers via seed registry')
        p.add_argument('--seed-registry', required=True, help='Seed registry base URL (e.g. http://IP:8787)')
        p.add_argument('--self-ip', required=True, help='This node reachable IP')
        p.add_argument('--port', type=int, default=8333, help='This node P2P port')
        p.add_argument('--max-peers', type=int, default=8, help='Max discovered peers to import')
        p.set_defaults(command='joinnetwork')

    async def get_info(self):
        return await self.handler.call('get_info')

    async def stop(self):
        return await self.handler.call('stop')

    async def help(self, command: Optional[str] = None):
        return await self.handler.call('help', command)

    async def ping(self):
        return await self.handler.call('ping')

    async def uptime(self):
        return await self.handler.call('uptime')

    async def get_network_info(self):
        return await self.handler.call('get_network_info')

    async def add_peer(self, address: str, mode: str = "addnode"):
        return await self.handler.call('add_peer', address, mode)

    async def list_peers(self, verbose: bool = False):
        return await self.handler.call('list_peers', bool(verbose))

    async def quick_join(self, address: str):
        connect_result = await self.handler.call('add_peer', address, 'connect')
        peers_result = await self.handler.call('list_peers', True)
        return {
            "join_attempt": connect_result,
            "peer_state": peers_result,
        }

    async def verify_peer(self, target: str, verifier_identity: str = "", verifier_node: str = "local"):
        return await self.handler.call('verify_peer', target, verifier_identity, verifier_node)

    async def join_network(
        self,
        seed_registry: str,
        self_ip: str,
        p2p_port: int = 8333,
        max_discovery_peers: int = 8,
    ):
        return await self.handler.call(
            'join_network',
            seed_registry,
            self_ip,
            int(p2p_port),
            int(max_discovery_peers),
        )
