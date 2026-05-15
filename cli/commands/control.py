"""Control CLI commands."""

import argparse
import socket
from urllib.parse import urlparse
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

        p = subparsers.add_parser('join-starter', help='Unified join command for starter node')
        p.add_argument('address', help='Starter node address in host:port')
        p.set_defaults(command='join-starter')

        p = subparsers.add_parser('doctor-network', help='Quick network troubleshooting report')
        p.add_argument('--peer', required=True, help='Peer address in host:port to diagnose')
        p.set_defaults(command='doctor-network')

        p = subparsers.add_parser('listpeers', help='List connected/static peers')
        p.add_argument('--verbose', action='store_true', help='Include detailed peer rows')
        p.set_defaults(command='listpeers')

        p = subparsers.add_parser('clearbanned', help='Clear all P2P bans')
        p.set_defaults(command='clearbanned')

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

    @staticmethod
    def _split_host_port(address: str, default_port: int = 8333) -> tuple[str, int]:
        raw = str(address or "").strip()
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

    async def join_starter(self, address: str):
        target = str(address or "").strip()
        host, port = self._split_host_port(target, 8333)
        if not host:
            return {"status": "FAILED", "reason": "invalid starter address", "target": target}

        # 1) clear temporary bans for this specific peer (best-effort)
        unban_results = []
        for candidate in {target, host, f"{host}:{int(port)}"}:
            try:
                res = await self.handler.call("setban", candidate, "remove")
                unban_results.append({"target": candidate, "result": res})
            except Exception as e:
                unban_results.append({"target": candidate, "error": str(e)})

        # 2) preflight TCP reachability
        try:
            with socket.create_connection((host, int(port)), timeout=3.0):
                preflight = {"ok": True, "host": host, "port": int(port)}
        except OSError as e:
            return {
                "status": "FAILED",
                "reason": f"preflight tcp failed: {e}",
                "target": f"{host}:{int(port)}",
                "preflight": {"ok": False, "host": host, "port": int(port)},
                "unban": unban_results,
            }

        # 3) run add_peer(connect) and list_peers
        connect_result = await self.handler.call("add_peer", f"{host}:{int(port)}", "connect")
        peers_result = await self.handler.call("list_peers", True)

        connected_rows = list(peers_result.get("connected_peers", []) or [])
        connected_addresses = {
            str(row.get("address", "")).strip() for row in connected_rows if isinstance(row, dict)
        }
        connected_now = bool(connect_result.get("connected_now")) if isinstance(connect_result, dict) else False
        connected = connected_now or (f"{host}:{int(port)}" in connected_addresses)
        if connected:
            return {
                "status": "CONNECTED",
                "target": f"{host}:{int(port)}",
                "preflight": preflight,
                "join_attempt": connect_result,
                "peer_state": peers_result,
                "unban": unban_results,
            }

        reason = "connect attempt finished but peer not in connected list"
        if isinstance(connect_result, dict) and connect_result.get("error"):
            reason = str(connect_result.get("error"))
        return {
            "status": "FAILED",
            "reason": reason,
            "target": f"{host}:{int(port)}",
            "preflight": preflight,
            "join_attempt": connect_result,
            "peer_state": peers_result,
            "unban": unban_results,
        }

    async def clear_banned(self):
        return await self.handler.call('clearbanned')

    async def doctor_network(self, peer: str):
        report = {
            "status": "OK",
            "checks": {},
            "peer": str(peer or "").strip(),
        }

        # 1) Node running (RPC TCP accept)
        rpc_host = "127.0.0.1"
        rpc_port = 8332
        try:
            parsed = urlparse(str(getattr(self.handler, "rpc_url", "") or ""))
            rpc_host = str(parsed.hostname or rpc_host)
            rpc_port = int(parsed.port or rpc_port)
        except Exception:
            pass

        try:
            with socket.create_connection((rpc_host, rpc_port), timeout=2.0):
                report["checks"]["node_running"] = {
                    "ok": True,
                    "rpc_endpoint": f"{rpc_host}:{rpc_port}",
                }
        except OSError as e:
            report["checks"]["node_running"] = {
                "ok": False,
                "rpc_endpoint": f"{rpc_host}:{rpc_port}",
                "error": str(e),
            }
            report["status"] = "FAILED"
            return report

        # 2) RPC cookie/auth + 3) P2P listening indicator via get_network_info
        network_info = None
        try:
            network_info = await self.handler.call("get_network_info")
            report["checks"]["rpc_cookie_auth"] = {"ok": True}
            report["checks"]["p2p_listening"] = {
                "ok": bool(network_info.get("network_active", False)),
                "connections": int(network_info.get("connections", 0)),
                "connections_in": int(network_info.get("connections_in", 0)),
                "connections_out": int(network_info.get("connections_out", 0)),
            }
        except Exception as e:
            report["checks"]["rpc_cookie_auth"] = {"ok": False, "error": str(e)}
            report["checks"]["p2p_listening"] = {
                "ok": False,
                "error": "get_network_info unavailable due to RPC/auth failure",
            }
            report["status"] = "FAILED"
            return report

        # 4) Peer reachable (TCP preflight)
        target = str(peer or "").strip()
        host, port = self._split_host_port(target, 8333)
        if not host:
            report["checks"]["peer_reachable"] = {"ok": False, "error": "invalid --peer value"}
            report["status"] = "FAILED"
        else:
            try:
                with socket.create_connection((host, int(port)), timeout=3.0):
                    report["checks"]["peer_reachable"] = {"ok": True, "target": f"{host}:{int(port)}"}
            except OSError as e:
                report["checks"]["peer_reachable"] = {
                    "ok": False,
                    "target": f"{host}:{int(port)}",
                    "error": str(e),
                }
                report["status"] = "FAILED"

        # 5) Ban status (best effort)
        ban_info = {"ok": True, "banned": False, "details": []}
        try:
            banned = await self.handler.call("listbanned")
            entries = list(banned if isinstance(banned, list) else [])
            target_keys = {target, host, f"{host}:{int(port)}"}
            hits = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                addr = str(item.get("address", "")).strip()
                if addr in target_keys:
                    hits.append(item)
            ban_info["banned"] = bool(hits)
            ban_info["details"] = hits
        except Exception as e:
            ban_info = {"ok": False, "error": str(e)}
        report["checks"]["ban_status"] = ban_info

        # 6) Handshake diagnostics (best effort from observability fields)
        handshake = {"ok": True, "disconnect_reasons": {}, "peer_present": False}
        try:
            admission = (
                network_info.get("admission_metrics", {})
                if isinstance(network_info, dict)
                else {}
            )
            if isinstance(admission, dict):
                handshake["disconnect_reasons"] = dict(admission.get("disconnect_reasons", {}) or {})
            peers = await self.handler.call("list_peers", True)
            peer_rows = list(peers.get("connected_peers", []) or []) if isinstance(peers, dict) else []
            target_addr = f"{host}:{int(port)}" if host else target
            handshake["peer_present"] = any(
                str(row.get("address", "")).strip() == target_addr
                for row in peer_rows
                if isinstance(row, dict)
            )
        except Exception as e:
            handshake = {"ok": False, "error": str(e)}
        report["checks"]["handshake_diagnostics"] = handshake

        if report["checks"].get("ban_status", {}).get("banned"):
            report["status"] = "FAILED"
        return report

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
