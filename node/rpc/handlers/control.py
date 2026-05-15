"""Control RPC handlers."""

import sys
import asyncio
import time
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger
from node.p2p.limits import OutboundClass

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
                'help',
                'add_peer',
                'list_peers',
                'verify_peer',
                'join_network',
                'verify_utxo_state',
                'check_storage_consistency',
            ],
            'description': "Control commands"
        }

    async def verify_utxo_state(self, max_mismatches: int = 20) -> Dict[str, Any]:
        chain = getattr(self.node, "chainstate", None)
        if chain is None:
            return {"ok": False, "error": "chainstate_not_initialized"}
        try:
            return chain.verify_active_chain_utxo_state(max_mismatches=int(max_mismatches))
        except Exception as e:
            return {"ok": False, "error": f"utxo_verify_failed: {e}"}

    async def check_storage_consistency(self, mode: str = "verify") -> Dict[str, Any]:
        chain = getattr(self.node, "chainstate", None)
        if chain is None:
            return {"ok": False, "error": "chainstate_not_initialized"}
        mode_norm = str(mode or "verify").strip().lower()
        if mode_norm not in {"fast", "verify", "recovery"}:
            return {"ok": False, "error": "mode must be fast|verify|recovery"}
        try:
            return chain.run_startup_consistency(mode_norm)
        except Exception as e:
            return {"ok": False, "error": f"storage_consistency_failed: {e}"}

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
        mempool = getattr(self.node, "mempool", None)

        if not connman:
            return {'error': 'Connection manager not initialized'}

        reject_counts = dict(getattr(mempool, "reject_reason_counts", {}) or {})
        tx_validation_rejects = {
            "zero_output": int(reject_counts.get("zero_output", 0)),
            "empty_output_script": int(reject_counts.get("empty_output_script", 0)),
            "negative_output": int(reject_counts.get("negative_output", 0)),
            "inputs_less_than_outputs": int(reject_counts.get("inputs_less_than_outputs", 0)),
            "script_verification_failed": int(reject_counts.get("script_verification_failed", 0)),
            "missing_utxo": int(reject_counts.get("missing_utxo", 0)),
        }

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
            ],
            'authority_chain': (
                connman.authority_chain.get_status()
                if getattr(connman, "authority_chain_enabled", False)
                else {"enabled": False}
            ),
            'admission_metrics': (
                connman.get_admission_metrics()
                if getattr(connman, "authority_chain_enabled", False)
                else {
                    "pending_join_count": 0,
                    "verify_latency_ms_avg": 0.0,
                    "verify_latency_samples": 0,
                    "rejection_reasons": {},
                    "verifier_activity": {},
                }
            ),
            "mempool_observability": {
                "last_reject_reason": getattr(mempool, "last_reject_reason", None),
                "reject_reason_counts": reject_counts,
                "tx_validation_rejects": tx_validation_rejects,
            },
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
        manager = getattr(self.node, "simple_wallet_manager", None)
        chainstate = getattr(self.node, "chainstate", None)
        if not manager or not chainstate:
            return 0
        return int(manager.get_balance(chainstate))

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

    async def get_health(self) -> Dict[str, Any]:
        """Detailed node health report."""
        checker = getattr(self.node, "health_checker", None)
        if checker is None:
            return {"status": "unknown", "message": "Health checker not initialized"}
        return await checker.check()

    async def get_readiness(self) -> Dict[str, Any]:
        """Readiness gate for load balancers and orchestration."""
        checker = getattr(self.node, "health_checker", None)
        if checker is None:
            return {"ready": False, "reason": "health_checker_missing"}
        return {"ready": bool(checker.is_ready())}

    async def get_metrics(self) -> Dict[str, Any]:
        """Node/system metrics snapshot."""
        collector = getattr(self.node, "metrics_collector", None)
        if collector is None:
            return {"error": "metrics collector not initialized"}
        return {
            "metrics": collector.get_metrics(),
            "rates": collector.get_rate(),
        }

    async def add_peer(self, address: str, mode: str = "addnode") -> Dict[str, Any]:
        """Add a peer address and optionally attempt immediate outbound connect."""
        connman = getattr(self.node, "connman", None)
        if not connman:
            return {"ok": False, "error": "Connection manager not initialized"}
        peer = str(address or "").strip()
        if not peer:
            return {"ok": False, "error": "address is required"}
        mode_norm = str(mode or "addnode").strip().lower()
        if mode_norm not in {"addnode", "connect"}:
            return {"ok": False, "error": "mode must be addnode|connect"}
        priority = 0 if mode_norm == "connect" else 10
        connman.addrman.add_static_peer(peer, priority=priority)
        connected_now = False
        if mode_norm == "connect":
            connected_now = await connman._connect_outbound_address(
                peer, OutboundClass.FULL_RELAY
            )
        return {
            "ok": True,
            "peer": peer,
            "mode": mode_norm,
            "connected_now": bool(connected_now),
        }

    async def list_peers(self, verbose: bool = False) -> Dict[str, Any]:
        """List connected peers and static discovery peers."""
        connman = getattr(self.node, "connman", None)
        if not connman:
            return {"ok": False, "error": "Connection manager not initialized"}
        connected = sorted(connman.peers.keys())
        static_peers = sorted(connman.addrman.get_static_peers())
        if not verbose:
            return {
                "ok": True,
                "connected": connected,
                "connected_count": len(connected),
                "static_peers": static_peers,
                "static_count": len(static_peers),
            }
        peer_rows = []
        for address, peer in sorted(connman.peers.items()):
            peer_rows.append(
                {
                    "address": address,
                    "outbound": bool(getattr(peer, "is_outbound", False)),
                    "connected": bool(getattr(peer, "connected", False)),
                    "peer_height": int(getattr(peer, "peer_height", 0)),
                    "relay_txs": bool(getattr(peer, "relay_txs", True)),
                }
            )
        return {
            "ok": True,
            "connected_peers": peer_rows,
            "connected_count": len(peer_rows),
            "static_peers": static_peers,
            "static_count": len(static_peers),
        }

    async def verify_peer(
        self,
        target: str,
        verifier_identity: str = "",
        verifier_node: str = "",
    ) -> Dict[str, Any]:
        """Record an authority-chain attestation for a candidate peer."""
        connman = getattr(self.node, "connman", None)
        if not connman:
            return {"ok": False, "error": "Connection manager not initialized"}
        if not getattr(connman, "authority_chain_enabled", False):
            return {"ok": False, "error": "authority_chain is disabled"}
        candidate = str(target or "").strip()
        if not candidate:
            return {"ok": False, "error": "target is required"}
        verifier = str(verifier_node or "local").strip()
        vid = str(verifier_identity or "").strip()
        accepted = connman.authority_chain.verify(
            verifier=verifier,
            target=candidate,
            verifier_identity=vid or None,
        )
        votes = connman.authority_chain.get_attestation_vote_count(candidate)
        return {
            "ok": True,
            "target": candidate,
            "accepted": bool(accepted),
            "votes": int(votes),
            "required_votes": int(connman.authority_chain.min_verifier_votes),
            "admission_mode": str(connman.authority_chain.admission_mode),
        }

    async def join_network(
        self,
        seed_registry: str,
        self_ip: str,
        p2p_port: int = 8333,
        max_discovery_peers: int = 8,
    ) -> Dict[str, Any]:
        """Register this node in seed registry and import verified peers."""
        connman = getattr(self.node, "connman", None)
        if not connman:
            return {"ok": False, "error": "Connection manager not initialized"}
        reg = str(seed_registry or "").strip()
        ip = str(self_ip or "").strip()
        if not reg:
            return {"ok": False, "error": "seed_registry is required"}
        if not ip:
            return {"ok": False, "error": "self_ip is required"}

        self_peer = f"{ip}:{int(p2p_port)}"
        body = json.dumps({"peer": self_peer}).encode("utf-8")
        req = urllib.request.Request(
            reg.rstrip("/") + "/register",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass

        peers_req = urllib.request.Request(
            reg.rstrip("/") + "/peers?status=verified",
            method="GET",
        )
        with urllib.request.urlopen(peers_req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        peers = payload.get("peers", []) if isinstance(payload, dict) else []
        imported = 0
        connected = 0
        for item in peers[: max(1, int(max_discovery_peers))]:
            peer = str(item or "").strip()
            if not peer or peer == self_peer:
                continue
            connman.addrman.add_static_peer(peer, priority=10)
            imported += 1
            if await connman._connect_outbound_address(peer, OutboundClass.FULL_RELAY):
                connected += 1
        return {
            "ok": True,
            "self_peer": self_peer,
            "imported_peers": int(imported),
            "connected_now": int(connected),
            "seed_registry": reg,
        }
