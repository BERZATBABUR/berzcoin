#!/usr/bin/env python3
"""Minimal peer seed registry server.

API:
- GET  /peers               -> {"peers": ["ip:port", ...]}
- POST /register {"peer": "ip:port"} -> {"ok": true}
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Tuple


class PeerRegistry:
    def __init__(self, db_path: Path):
        self.db_path = db_path
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
            return {"peers": peers, "updated_at": int(time.time())}
        except Exception:
            return {"peers": {}, "updated_at": int(time.time())}

    def _save(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")

    def register(self, peer: str) -> None:
        now = int(time.time())
        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            peers[peer] = {"last_seen": now}
            self._state["updated_at"] = now
            self._save()

    def list_peers(self, ttl_seconds: int) -> List[str]:
        now = int(time.time())
        with self._lock:
            peers = self._state.setdefault("peers", {})
            assert isinstance(peers, dict)
            alive: List[str] = []
            changed = False
            for peer, meta in list(peers.items()):
                last_seen = 0
                if isinstance(meta, dict):
                    last_seen = int(meta.get("last_seen", 0))
                if now - last_seen <= ttl_seconds:
                    alive.append(peer)
                else:
                    peers.pop(peer, None)
                    changed = True
            if changed:
                self._state["updated_at"] = now
                self._save()
            return sorted(alive)


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
            if self.path != "/peers":
                self._write_json(404, {"error": "not found"})
                return
            self._write_json(200, {"peers": registry.list_peers(ttl_seconds)})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/register":
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
            registry.register(peer)
            self._write_json(200, {"ok": True})

        def log_message(self, fmt: str, *args: Tuple[object, ...]) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="BerzCoin seed registry server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default="~/.berzcoin/seed_registry_server.json")
    parser.add_argument("--ttl-seconds", type=int, default=86400)
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    registry = PeerRegistry(db_path)
    handler = make_handler(registry, ttl_seconds=int(args.ttl_seconds))
    server = ThreadingHTTPServer((args.host, int(args.port)), handler)
    print(f"Seed registry listening on http://{args.host}:{args.port}")
    print(f"DB: {db_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()

