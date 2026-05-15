#!/usr/bin/env python3
"""Peer seed registry server with admission-aware verification.

API:
- GET  /peers                             -> {"peers": ["ip:port", ...]} (verified only)
- GET  /peers?status=verified             -> filter by status (pending|verified|rejected)
- GET  /peers?all=1                       -> include metadata
- GET  /peer/<ip:port>                    -> metadata for one peer
- POST /register {"peer": "ip:port"}      -> register and attempt verification
- POST /attest {"peer":"ip:port","verifier_id":"...","status":"verified|rejected","reason":"..."}
- POST /approve {"peer":"ip:port"}        -> force verify peer (legacy compatibility)
- POST /reject {"peer":"ip:port","reason":"..."} -> reject peer
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote
from pathlib import Path
from typing import Dict, List, Tuple

VALID_STATUSES = {"pending", "verified", "rejected"}


class PeerRegistry:
    def __init__(
        self,
        db_path: Path,
        *,
        require_reachable: bool = True,
        probe_timeout_secs: float = 1.5,
        allow_private_ip: bool = True,
    ):
        self.db_path = db_path
        self.require_reachable = bool(require_reachable)
        self.probe_timeout_secs = float(probe_timeout_secs)
        self.allow_private_ip = bool(allow_private_ip)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> Dict[str, object]:
        if not self.db_path.exists():
            return {"peers": {}, "updated_at": int(time.time())}
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"peers": {}, "updated_at": int(time.time())}
            peers = data.get("peers", {})
            if not isinstance(peers, dict):
                peers = {}
            # Back-compat: normalize old list format to dict metadata.
            if isinstance(peers, list):
                now = int(time.time())
                peers = {
                    str(p): {
                        "status": "verified",
                        "first_seen": now,
                        "last_seen": now,
                        "reason": "legacy_approved",
                        "attestations": [],
                    }
                    for p in peers
                }
            # Back-compat: migrate approved->verified and ensure attestation container.
            for peer, meta in list(peers.items()):
                if not isinstance(meta, dict):
                    peers[peer] = {"status": "pending", "first_seen": int(time.time()), "last_seen": int(time.time())}
                    meta = peers[peer]
                status = str(meta.get("status", "pending")).strip().lower()
                if status == "approved":
                    status = "verified"
                if status not in VALID_STATUSES:
                    status = "pending"
                meta["status"] = status
                if not isinstance(meta.get("attestations"), list):
                    meta["attestations"] = []
            return {"peers": peers, "updated_at": int(time.time())}
        except Exception:
            return {"peers": {}, "updated_at": int(time.time())}

    def _save(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _parse_peer(peer: str) -> Tuple[str, int]:
        if ":" not in peer:
            raise ValueError("peer must be host:port")
        host, port_text = peer.rsplit(":", 1)
        host = host.strip()
        if not host:
            raise ValueError("host is empty")
        try:
            port = int(port_text)
        except ValueError as e:
            raise ValueError("port must be integer") from e
        if port < 1 or port > 65535:
            raise ValueError("port out of range")
        return host, port

    def _validate_host_policy(self, host: str) -> None:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # Hostnames are allowed; DNS resolution is part of probe.
            return
        if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError("ip class not allowed")
        if (ip.is_private or ip.is_loopback or ip.is_link_local) and not self.allow_private_ip:
            raise ValueError("private/loopback/link-local ip not allowed")

    def _probe_reachable(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=self.probe_timeout_secs):
                return True
        except OSError:
            return False

    def register(self, peer: str) -> Dict[str, object]:
        now = int(time.time())
        host, port = self._parse_peer(peer)
        self._validate_host_policy(host)
        peer = f"{host}:{port}"
        reachable = self._probe_reachable(host, port) if self.require_reachable else True
        status = "verified" if reachable else "pending"
        reason = "probe_ok" if reachable else "unreachable"

        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            prev = peers.get(peer, {})
            if not isinstance(prev, dict):
                prev = {}
            peers[peer] = {
                "first_seen": int(prev.get("first_seen", now)),
                "last_seen": now,
                "status": status,
                "reason": reason,
                "reachable": bool(reachable),
                "last_probe_at": now,
                "attestations": prev.get("attestations", []) if isinstance(prev.get("attestations"), list) else [],
            }
            self._state["updated_at"] = now
            self._save()
            return {"peer": peer, "status": status, "reason": reason}

    def _sweep_expired(self, ttl_seconds: int) -> None:
        now = int(time.time())
        peers = self._state.setdefault("peers", {})
        assert isinstance(peers, dict)
        changed = False
        for peer, meta in list(peers.items()):
            last_seen = 0
            if isinstance(meta, dict):
                last_seen = int(meta.get("last_seen", 0))
            if now - last_seen > ttl_seconds:
                peers.pop(peer, None)
                changed = True
        if changed:
            self._state["updated_at"] = now
            self._save()

    def list_peers(
        self,
        ttl_seconds: int,
        include_all: bool = False,
        status_filter: str = "",
    ) -> List[object]:
        with self._lock:
            self._sweep_expired(ttl_seconds)
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            normalized_filter = str(status_filter or "").strip().lower()
            if normalized_filter == "approved":
                normalized_filter = "verified"
            if normalized_filter and normalized_filter not in VALID_STATUSES:
                normalized_filter = ""
            if include_all:
                out: List[object] = []
                for peer in sorted(peers.keys()):
                    meta = peers.get(peer, {})
                    if not isinstance(meta, dict):
                        meta = {}
                    status = str(meta.get("status", "pending")).strip().lower()
                    if status == "approved":
                        status = "verified"
                    if normalized_filter and status != normalized_filter:
                        continue
                    out.append({"peer": peer, **meta})
                return out
            verified = []
            for peer, meta in peers.items():
                status = str(meta.get("status", "pending")).strip().lower() if isinstance(meta, dict) else "pending"
                if status == "approved":
                    status = "verified"
                if normalized_filter:
                    if status == normalized_filter:
                        verified.append(peer)
                    continue
                if status == "verified":
                    verified.append(peer)
            return sorted(verified)

    def get_peer(self, peer: str) -> Dict[str, object]:
        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            meta = peers.get(peer)
            if not isinstance(meta, dict):
                raise KeyError(peer)
            return {"peer": peer, **meta}

    def set_peer_status(self, peer: str, status: str, reason: str = "") -> Dict[str, object]:
        now = int(time.time())
        status = str(status).strip().lower()
        if status == "approved":
            status = "verified"
        if status not in VALID_STATUSES:
            raise ValueError("invalid status")
        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            meta = peers.get(peer)
            if not isinstance(meta, dict):
                raise KeyError(peer)
            meta["status"] = status
            meta["reason"] = str(reason or "")
            meta["last_seen"] = int(meta.get("last_seen", now))
            meta["last_probe_at"] = now
            if status == "verified":
                meta["reachable"] = True
            if not isinstance(meta.get("attestations"), list):
                meta["attestations"] = []
            self._state["updated_at"] = now
            self._save()
            return {"peer": peer, **meta}

    def attest(
        self,
        peer: str,
        verifier_id: str,
        status: str = "verified",
        reason: str = "",
    ) -> Dict[str, object]:
        now = int(time.time())
        peer = str(peer or "").strip()
        verifier_id = str(verifier_id or "").strip()
        status = str(status or "verified").strip().lower()
        if status == "approved":
            status = "verified"
        if status not in {"verified", "rejected"}:
            raise ValueError("attest status must be verified|rejected")
        if not peer or ":" not in peer:
            raise ValueError("peer must be ip:port")
        if not verifier_id:
            raise ValueError("verifier_id is required")
        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            meta = peers.get(peer)
            if not isinstance(meta, dict):
                raise KeyError(peer)
            att_list = meta.get("attestations")
            if not isinstance(att_list, list):
                att_list = []
            att_list.append(
                {
                    "verifier_id": verifier_id,
                    "status": status,
                    "reason": str(reason or ""),
                    "timestamp": now,
                }
            )
            # Latest vote by verifier wins.
            latest_by_verifier: Dict[str, Dict[str, object]] = {}
            for item in att_list:
                if not isinstance(item, dict):
                    continue
                vid = str(item.get("verifier_id", "")).strip()
                if not vid:
                    continue
                latest_by_verifier[vid] = item
            verified_votes = 0
            rejected_votes = 0
            reasons: List[str] = []
            for vote in latest_by_verifier.values():
                vstatus = str(vote.get("status", "")).strip().lower()
                vreason = str(vote.get("reason", "")).strip()
                if vstatus == "verified":
                    verified_votes += 1
                elif vstatus == "rejected":
                    rejected_votes += 1
                    if vreason:
                        reasons.append(vreason)

            meta["attestations"] = att_list
            meta["vote_summary"] = {
                "verified": int(verified_votes),
                "rejected": int(rejected_votes),
                "total_unique_verifiers": int(len(latest_by_verifier)),
            }
            if rejected_votes > 0 and verified_votes == 0:
                meta["status"] = "rejected"
                meta["reason"] = "; ".join(reasons[:3]) if reasons else "attested_rejected"
            elif verified_votes > 0 and rejected_votes == 0:
                meta["status"] = "verified"
                meta["reason"] = "attested_verified"
                meta["reachable"] = True
            else:
                meta["status"] = "pending"
                meta["reason"] = "conflicting_attestations"
            meta["last_seen"] = int(meta.get("last_seen", now))
            meta["last_attested_at"] = now
            self._state["updated_at"] = now
            self._save()
            return {"peer": peer, **meta}


def make_handler(registry: PeerRegistry, ttl_seconds: int):
    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, code: int, payload: Dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/peers":
                qs = parse_qs(parsed.query or "")
                include_all = qs.get("all", ["0"])[0] in {"1", "true", "yes"}
                status_filter = str(qs.get("status", [""])[0] or "").strip().lower()
                if include_all:
                    self._write_json(
                        200,
                        {
                            "peers": registry.list_peers(
                                ttl_seconds,
                                include_all=True,
                                status_filter=status_filter,
                            )
                        },
                    )
                else:
                    self._write_json(
                        200,
                        {
                            "peers": registry.list_peers(
                                ttl_seconds,
                                include_all=False,
                                status_filter=status_filter,
                            )
                        },
                    )
                return
            if parsed.path.startswith("/peer/"):
                peer = unquote(parsed.path[len("/peer/"):]).strip()
                try:
                    payload = registry.get_peer(peer)
                except KeyError:
                    self._write_json(404, {"error": "peer not found"})
                    return
                self._write_json(200, payload)
                return
            if parsed.path == "/health":
                self._write_json(200, {"ok": True, "updated_at": int(time.time())})
                return
            else:
                self._write_json(404, {"error": "not found"})
                return

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/register", "/approve", "/reject", "/attest"):
                self._write_json(404, {"error": "not found"})
                return
            content_len = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                self._write_json(400, {"error": "invalid json"})
                return
            peer = str(payload.get("peer", "")).strip() if isinstance(payload, dict) else ""
            if not peer or ":" not in peer:
                self._write_json(400, {"error": "peer must be ip:port"})
                return
            if self.path == "/register":
                try:
                    result = registry.register(peer)
                except ValueError as e:
                    self._write_json(400, {"ok": False, "error": str(e)})
                    return
                self._write_json(200, {"ok": True, **result})
                return
            if self.path == "/approve":
                try:
                    result = registry.set_peer_status(peer, "verified", reason="manual_approve")
                except KeyError:
                    self._write_json(404, {"ok": False, "error": "peer not found"})
                    return
                self._write_json(200, {"ok": True, **result})
                return
            if self.path == "/reject":
                reason = str(payload.get("reason", "manual_reject")).strip() if isinstance(payload, dict) else "manual_reject"
                try:
                    result = registry.set_peer_status(peer, "rejected", reason=reason or "manual_reject")
                except KeyError:
                    self._write_json(404, {"ok": False, "error": "peer not found"})
                    return
                self._write_json(200, {"ok": True, **result})
                return
            if self.path == "/attest":
                verifier_id = str(payload.get("verifier_id", "")).strip() if isinstance(payload, dict) else ""
                status = str(payload.get("status", "verified")).strip() if isinstance(payload, dict) else "verified"
                reason = str(payload.get("reason", "")).strip() if isinstance(payload, dict) else ""
                try:
                    result = registry.attest(peer, verifier_id=verifier_id, status=status, reason=reason)
                except KeyError:
                    self._write_json(404, {"ok": False, "error": "peer not found"})
                    return
                except ValueError as e:
                    self._write_json(400, {"ok": False, "error": str(e)})
                    return
                self._write_json(200, {"ok": True, **result})
                return

        def log_message(self, fmt: str, *args: Tuple[object, ...]) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="BerzCoin seed registry server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default="~/.berzcoin/seed_registry_server.json")
    parser.add_argument("--ttl-seconds", type=int, default=86400)
    parser.add_argument(
        "--no-require-reachable",
        action="store_true",
        help="Disable registration TCP reachability probe; auto-approve all valid peers.",
    )
    parser.add_argument(
        "--probe-timeout-secs",
        type=float,
        default=1.5,
        help="TCP probe timeout per peer during registration (default 1.5).",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Reject private/loopback/link-local IP registrations.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    registry = PeerRegistry(
        db_path,
        require_reachable=not bool(args.no_require_reachable),
        probe_timeout_secs=float(args.probe_timeout_secs),
        allow_private_ip=not bool(args.public_only),
    )
    handler = make_handler(registry, ttl_seconds=int(args.ttl_seconds))
    server = ThreadingHTTPServer((args.host, int(args.port)), handler)
    print(f"Seed registry listening on http://{args.host}:{args.port}")
    print(f"DB: {db_path}")
    print(f"Reachability probe: {'on' if not args.no_require_reachable else 'off'}")
    print(f"Allow private IP: {'no' if args.public_only else 'yes'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
