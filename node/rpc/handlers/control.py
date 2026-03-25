"""Control RPC handlers."""

import sys
import asyncio
import time
from typing import Any, Dict, Optional

from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger

logger = get_logger()


class ControlHandlers:
    """RPC handlers for control commands."""

    def __init__(self, node: Any):
        self.node = node
        self.start_time = time.time()

    async def get_info(self) -> Dict[str, Any]:
        """Get node information."""
        chain = self.node.chainstate
        best_h = chain.get_best_height()
        best_hash = chain.get_best_block_hash()

        return {
            'version': 1000000,
            'protocol_version': 70015,
            'network': getattr(self.node, 'network', 'mainnet'),
            'blocks': best_h,
            'best_block_hash': best_hash,
            'balance': await self._get_total_balance(),
            'connections': len(self.node.connman.peers) if getattr(self.node, 'connman', None) else 0,
            'difficulty': self._get_difficulty(),
            'time_offset': 0,
            'warnings': '',
            'uptime': int(time.time() - self.start_time)
        }

    async def stop(self) -> str:
        """Stop the node."""
        logger.info("Stopping node via RPC...")
        asyncio.create_task(self._shutdown())
        return "BerzCoin server stopping"

    async def help(self, command: Optional[str] = None) -> Dict[str, Any]:
        """RPC command help."""
        if command:
            return {
                'command': command,
                'description': f"Help for {command}",
                'params': []
            }

        return {
            'commands': [
                'get_info',
                'stop',
                'help'
            ],
            'description': "Control commands"
        }

    async def get_memory_info(self) -> Dict[str, Any]:
        """Memory usage (requires psutil)."""
        try:
            import psutil
            process = psutil.Process()
            memory = process.memory_info()
            return {
                'rss': memory.rss,
                'vms': memory.vms,
                'percent': process.memory_percent(),
                'peak_rss': getattr(memory, 'peak_wset', memory.rss)
            }
        except ImportError:
            return {'error': 'psutil not installed'}

    async def get_network_info(self) -> Dict[str, Any]:
        """P2P connection summary."""
        connman = getattr(self.node, 'connman', None)

        if not connman:
            return {'error': 'Connection manager not initialized'}

        return {
            'version': 70015,
            'subversion': '/BerzCoin:1.0/',
            'protocol_version': 70015,
            'local_services': '00000001',
            'local_relay': True,
            'time_offset': 0,
            'connections': len(connman.peers),
            'connections_in': len(connman.inbound_peers),
            'connections_out': len(connman.outbound_peers),
            'network_active': True,
            'networks': [
                {
                    'name': 'ipv4',
                    'limited': False,
                    'reachable': True,
                    'proxy': '',
                    'proxy_randomize_credentials': False
                }
            ]
        }

    async def get_difficulty(self) -> float:
        """Current difficulty."""
        return self._get_difficulty()

    def _get_difficulty(self) -> float:
        chain = self.node.chainstate
        best_hash = chain.get_best_block_hash()

        if not best_hash:
            return 1.0

        header = chain.get_header_by_hash(best_hash)
        if not header:
            return 1.0

        pow_check = ProofOfWork(chain.params)
        return pow_check.calculate_difficulty(header.bits)

    async def _get_total_balance(self) -> int:
        if not hasattr(self.node, 'wallet') or not self.node.wallet:
            return 0

        return self.node.wallet.get_balance()

    async def _shutdown(self) -> None:
        await asyncio.sleep(1)
        if hasattr(self.node, 'stop') and callable(self.node.stop):
            res = self.node.stop()
            if asyncio.iscoroutine(res):
                await res
        else:
            sys.exit(0)

    async def ping(self) -> str:
        return "pong"

    async def uptime(self) -> int:
        return int(time.time() - self.start_time)
