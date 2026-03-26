"""
Enhanced web dashboard for BerzCoin (wallet + mining control).

This module is intentionally self-contained and uses the node's existing
objects (wallet, miner, chainstate, mempool). It provides extra UI pages
on top of the API surface expected by the dashboard demo scripts.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict, Optional

from aiohttp import web
from aiohttp.web import json_response

from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger

logger = get_logger()


class EnhancedDashboard:
    """A richer web UI than `node/web/dashboard.py`."""

    def __init__(
        self,
        node: Any,
        host: str = "127.0.0.1",
        port: int = 8080,
        require_auth: bool = False,
    ) -> None:
        self.node = node
        self.host = host
        self.port = port
        # Accept the `require_auth` parameter for compatibility with callers,
        # but keep auth disabled by default here so the login page is skipped.
        # To enable auth, either remove this override or change it to use the
        # provided `require_auth` value.
        self.require_auth = False
        self.auth_token = None

        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None

        self._monitor_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self.app = web.Application(middlewares=[self._auth_middleware])

        # HTML pages
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/wallet", self.wallet_page)
        self.app.router.add_get("/mining", self.mining_page)
        self.app.router.add_get("/mempool", self.mempool_page)
        self.app.router.add_get("/transactions", self.transactions_page)
        self.app.router.add_get("/login", self.login_page)

        # API routes
        self.app.router.add_get("/api/auth/info", self.api_auth_info)
        self.app.router.add_get("/api/status", self.api_status)
        self.app.router.add_get("/api/blockchain", self.api_blockchain)
        self.app.router.add_get("/api/peers", self.api_peers)
        self.app.router.add_post("/api/peers/add", self.api_peers_add)

        self.app.router.add_get("/api/wallet/info", self.api_wallet_info)
        self.app.router.add_get("/api/wallet/keys", self.api_wallet_keys)
        self.app.router.add_post("/api/wallet/create", self.api_wallet_create)
        self.app.router.add_post("/api/wallet/load", self.api_wallet_load)
        self.app.router.add_post("/api/wallet/unlock", self.api_wallet_unlock)
        # Unlock via private key import (convenience for regtest/dev)
        self.app.router.add_post("/api/wallet/unlock_key", self.api_wallet_unlock_key)
        # Import a private key without unlocking
        self.app.router.add_post("/api/wallet/import_key", self.api_wallet_import_key)
        self.app.router.add_post("/api/wallet/lock", self.api_wallet_lock)
        self.app.router.add_post("/api/wallet/address", self.api_create_address)
        self.app.router.add_post("/api/wallet/send", self.api_send_transaction)
        self.app.router.add_get("/api/wallet/balance", self.api_wallet_balance)
        self.app.router.add_get("/api/wallet/utxos", self.api_wallet_utxos)

        self.app.router.add_get("/api/mining/info", self.api_mining_info)
        self.app.router.add_post("/api/mining/start", self.api_mining_start)
        self.app.router.add_post("/api/mining/stop", self.api_mining_stop)
        self.app.router.add_get("/api/mining/address", self.api_mining_address)
        self.app.router.add_post("/api/mining/address", self.api_set_mining_address)
        # Regtest: generate N blocks immediately (uses internal RPC handler)
        self.app.router.add_post("/api/mining/generate", self.api_mining_generate)

        self.app.router.add_get("/api/mempool/info", self.api_mempool_info)
        self.app.router.add_get("/api/mempool/txs", self.api_mempool_txs)
        self.app.router.add_post("/api/mempool/submit", self.api_submit_transaction)

        self.app.router.add_get("/api/transactions", self.api_transactions)

        # Runner
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

        logger.info("Enhanced dashboard started on http://%s:%s", self.host, self.port)
        if self.require_auth and self.auth_token:
            logger.warning(
                "Dashboard auth enabled. Token: %s (send as X-Auth-Token header or auth_token cookie)",
                self.auth_token,
            )

        # Optional background monitor (non-blocking).
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def _monitor_loop(self) -> None:
        # Keep the loop lightweight; the UI polls anyway.
        while True:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return

    # ========== AUTH ==========

    def _get_auth_token_from_request(self, request: web.Request) -> Optional[str]:
        token = request.headers.get("X-Auth-Token")
        if token:
            return token
        return request.cookies.get("auth_token")

    async def _check_auth(self, request: web.Request) -> bool:
        if not self.require_auth:
            return True
        if not self.auth_token:
            return False
        return self._get_auth_token_from_request(request) == self.auth_token

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if not self.require_auth:
            return await handler(request)

        if request.path in ("/login", "/api/auth/info"):
            return await handler(request)

        if not await self._check_auth(request):
            if request.path.startswith("/api/"):
                return web.json_response({"error": "Unauthorized"}, status=401)
            raise web.HTTPFound("/login")

        return await handler(request)

    # ========== HTML ==========

    async def index(self, request: web.Request) -> web.Response:
        _ = request
        return web.Response(text=self._get_main_html(), content_type="text/html")

    async def wallet_page(self, request: web.Request) -> web.Response:
        _ = request
        return web.Response(text=self._get_wallet_html(), content_type="text/html")

    async def mining_page(self, request: web.Request) -> web.Response:
        _ = request
        return web.Response(text=self._get_mining_html(), content_type="text/html")

    async def mempool_page(self, request: web.Request) -> web.Response:
        _ = request
        return web.Response(text=self._get_mempool_html(), content_type="text/html")

    async def transactions_page(self, request: web.Request) -> web.Response:
        _ = request
        return web.Response(text=self._get_tx_html(), content_type="text/html")

    async def login_page(self, request: web.Request) -> web.Response:
        # If token is provided via querystring, set it as a cookie for browser use.
        token = request.query.get("token")
        if self.require_auth and self.auth_token and token == self.auth_token:
            resp = web.Response(
                text=self._get_login_html(authed=True),
                content_type="text/html",
            )
            resp.set_cookie("auth_token", self.auth_token, httponly=True, samesite="Lax")
            return resp
        return web.Response(text=self._get_login_html(authed=False), content_type="text/html")

    def _get_main_html(self) -> str:
        return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>BerzCoin Enhanced Dashboard</title>
  <style>
    body { font-family: monospace; margin: 20px; background: #0a0a0a; color: #00ff00; }
    .container { max-width: 1100px; margin: 0 auto; }
    .card { background: #1a1a1a; border: 1px solid #00ff00; border-radius: 6px; padding: 16px; margin: 12px 0; }
    button { background: #00ff00; color: #0a0a0a; padding: 10px 14px; margin-top: 8px; border: none; cursor: pointer; font-weight: bold; }
    input { background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; width: 100%; margin-top: 6px; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .col { flex: 1; min-width: 320px; }
    pre { white-space: pre-wrap; word-break: break-word; }
    a { color: #00ff00; }
    .nav { margin: 10px 0 18px 0; }
    .nav a { margin-right: 14px; }
  </style>
</head>
<body>
<div class="container">
  <h1>BerzCoin Enhanced Dashboard</h1>
  <div class="nav">
    <a href="/">Home</a>
    <a href="/wallet">Wallet</a>
    <a href="/mining">Mining</a>
    <a href="/mempool">Mempool</a>
    <a href="/transactions">Transactions</a>
    <a href="/login">Login</a>
  </div>
  <div class="row">
    <div class="card col">
      <h3>Node Status</h3>
      <pre id="status">Loading...</pre>
    </div>
    <div class="card col">
      <h3>Blockchain</h3>
      <pre id="chain">Loading...</pre>
    </div>
        <div class="card col">
            <h3>Peers</h3>
            <input id="peeraddr" placeholder="host:port (e.g. 127.0.0.1:8333)">
            <button onclick="addPeer()">Add / Connect</button>
            <pre id="peers">Loading...</pre>
        </div>
  </div>
  <div class="row">
    <div class="card col">
      <h3>Wallet</h3>
      <pre id="wallet">Loading...</pre>
    </div>
    <div class="card col">
      <h3>Mining</h3>
      <pre id="mining">Loading...</pre>
      <button onclick="startMining()">Start mining</button>
      <button onclick="stopMining()">Stop mining</button>
      <pre id="miningAction" style="margin-top:10px;"></pre>
    </div>
  </div>
</div>
<script>
async function refresh() {
  const s = await fetch('/api/status'); document.getElementById('status').textContent = JSON.stringify(await s.json(), null, 2);
  const c = await fetch('/api/blockchain'); document.getElementById('chain').textContent = JSON.stringify(await c.json(), null, 2);
  const w = await fetch('/api/wallet/info'); document.getElementById('wallet').textContent = JSON.stringify(await w.json(), null, 2);
  const m = await fetch('/api/mining/info'); document.getElementById('mining').textContent = JSON.stringify(await m.json(), null, 2);
    const p = await fetch('/api/peers'); document.getElementById('peers').textContent = JSON.stringify(await p.json(), null, 2);
}
async function startMining() {
  const resp = await fetch('/api/mining/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
  const data = await resp.json().catch(() => ({}));
  document.getElementById('miningAction').textContent = JSON.stringify(data, null, 2);
  await refresh();
}
async function stopMining() {
  const resp = await fetch('/api/mining/stop', {method:'POST'});
  const data = await resp.json().catch(() => ({}));
  document.getElementById('miningAction').textContent = JSON.stringify(data, null, 2);
  await refresh();
}
refresh();
setInterval(refresh, 3000);
async function addPeer(){
    const addr = document.getElementById('peeraddr').value.trim();
    if(!addr) return;
    const r = await fetch('/api/peers/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:addr})});
    const res = await r.json().catch(() => ({}));
    document.getElementById('peers').textContent = JSON.stringify(res, null, 2);
    await refresh();
}
</script>
</body>
</html>
"""

    def _get_wallet_html(self) -> str:
        return """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Wallet</title>
<style>
  body { font-family: monospace; margin: 20px; background: #0a0a0a; color: #00ff00; }
  .card { background: #1a1a1a; border: 1px solid #00ff00; border-radius: 6px; padding: 16px; margin: 12px 0; }
  input { background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; width: 100%; margin-top: 6px; }
  button { background: #00ff00; color: #0a0a0a; padding: 10px 14px; margin-top: 8px; border: none; cursor: pointer; font-weight: bold; }
  pre { white-space: pre-wrap; word-break: break-word; }
</style></head>
<body>
<h1>Wallet Control</h1>

<div class="card">
  <h3>Load Existing Wallet</h3>
  <input id="loadName" placeholder="wallet name (default: main_wallet)">
  <input id="loadPw" type="password" placeholder="wallet password">
  <button onclick="loadWallet()">Load</button>
</div>

<div class="card">
  <h3>Create New Wallet</h3>
  <input id="createName" placeholder="wallet name (default: main_wallet)">
  <input id="createPw" type="password" placeholder="new wallet password">
  <input id="createPriv" placeholder="(optional) import private key hex">
  <button onclick="createWallet()">Create</button>
  <pre id="createRes"></pre>
</div>

<div class="card">
  <h3>Unlock</h3>
  <input id="pw" type="password" placeholder="walletpassphrase">
  <button onclick="unlock()">Unlock</button>
  <button onclick="lockWallet()" style="background:#ff4444;color:white;">Lock</button>
</div>
<div class="card">
  <h3>Create New Address</h3>
  <button onclick="newAddress()">Generate</button>
  <pre id="addr"></pre>
</div>
<div class="card">
  <h3>Send</h3>
  <input id="to" placeholder="recipient address">
  <input id="amount" placeholder="amount in BERZ" type="number" step="any">
  <button onclick="send()">Send</button>
  <pre id="sendres"></pre>
</div>
<div class="card">
  <h3>Balance + UTXOs</h3>
  <pre id="wallet">Loading...</pre>
</div>
<script>
async function createWallet(){
  const wallet_name = document.getElementById('createName').value || 'main_wallet';
  const password = document.getElementById('createPw').value;
  const private_key = document.getElementById('createPriv').value;
  const r = await fetch('/api/wallet/create', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({wallet_name, password, private_key: private_key || null})});
  const data = await r.json();
  document.getElementById('createRes').textContent = JSON.stringify(data, null, 2);
  await refresh();
}
async function loadWallet(){
  const wallet_name = document.getElementById('loadName').value || 'main_wallet';
  const password = document.getElementById('loadPw').value;
  const r = await fetch('/api/wallet/load', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({wallet_name, password})});
  const data = await r.json();
  document.getElementById('createRes').textContent = JSON.stringify(data, null, 2);
  await refresh();
}
async function unlock(){
  const r= await fetch('/api/wallet/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('pw').value})});
  document.getElementById('sendres').textContent = JSON.stringify(await r.json(),null,2);
  await refresh();
}
async function lockWallet(){
  const r= await fetch('/api/wallet/lock',{method:'POST'});
  await refresh();
}
async function newAddress(){
  const r= await fetch('/api/wallet/address',{method:'POST'});
  document.getElementById('addr').textContent = JSON.stringify(await r.json(),null,2);
  await refresh();
}
async function send(){
  const r= await fetch('/api/wallet/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to:document.getElementById('to').value,amount:parseFloat(document.getElementById('amount').value)})});
  document.getElementById('sendres').textContent = JSON.stringify(await r.json(),null,2);
  await refresh();
}
async function refresh(){
  const r= await fetch('/api/wallet/info'); document.getElementById('wallet').textContent = JSON.stringify(await r.json(),null,2);
}
refresh();
</script>
</body>
</html>
"""

    def _get_mining_html(self) -> str:
        return """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Mining</title>
<style>
  body { font-family: monospace; margin: 20px; background: #0a0a0a; color: #00ff00; }
  .card { background: #1a1a1a; border: 1px solid #00ff00; border-radius: 6px; padding: 16px; margin: 12px 0; }
  input { background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; width: 100%; margin-top: 6px; }
  button { background: #00ff00; color: #0a0a0a; padding: 10px 14px; margin-top: 8px; border: none; cursor: pointer; font-weight: bold; }
  pre { white-space: pre-wrap; word-break: break-word; }
  a { color: #00ff00; }
  .nav { margin: 10px 0 18px 0; }
  .nav a { margin-right: 14px; }
</style></head>
<body>
<h1>Mining Control</h1>
<div class="nav">
  <a href="/">Home</a>
  <a href="/wallet">Wallet</a>
  <a href="/mining">Mining</a>
  <a href="/mempool">Mempool</a>
  <a href="/transactions">Transactions</a>
</div>
<div class="card">
  <h3>Mining Address</h3>
  <input id="addr" placeholder="set mining address (regtest)">
  <button onclick="setAddr()">Set</button>
</div>
<div class="card">
  <h3>Mining</h3>
  <button onclick="start()">Start</button>
  <button onclick="stop()" style="background:#ff4444;color:white;">Stop</button>
  <pre id="mining">Loading...</pre>
  <pre id="action" style="margin-top:10px;"></pre>
</div>
<script>
async function start(){
  const resp = await fetch('/api/mining/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
  document.getElementById('action').textContent = JSON.stringify(await resp.json().catch(() => ({})), null, 2);
  await refresh();
}
async function stop(){
  const resp = await fetch('/api/mining/stop',{method:'POST'});
  document.getElementById('action').textContent = JSON.stringify(await resp.json().catch(() => ({})), null, 2);
  await refresh();
}
async function setAddr(){
  await fetch('/api/mining/address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:document.getElementById('addr').value})});
  await refresh();
}
async function refresh(){
  const r= await fetch('/api/mining/info'); document.getElementById('mining').textContent = JSON.stringify(await r.json(),null,2);
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""

    def _get_mempool_html(self) -> str:
        return """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Mempool</title>
<style>
  body { font-family: monospace; margin: 20px; background: #0a0a0a; color: #00ff00; }
  .card { background: #1a1a1a; border: 1px solid #00ff00; border-radius: 6px; padding: 16px; margin: 12px 0; }
  input, textarea { background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; width: 100%; margin-top: 6px; }
  button { background: #00ff00; color: #0a0a0a; padding: 10px 14px; margin-top: 8px; border: none; cursor: pointer; font-weight: bold; }
  pre { white-space: pre-wrap; word-break: break-word; }
</style></head>
<body>
<h1>Mempool</h1>
<div class="card">
  <h3>Info</h3>
  <pre id="info">Loading...</pre>
</div>
<div class="card">
  <h3>Transactions</h3>
  <pre id="txs">Loading...</pre>
</div>
<div class="card">
  <h3>Submit Raw Transaction</h3>
  <textarea id="hex" rows="4" placeholder="transaction hex"></textarea>
  <button onclick="submit()">Submit</button>
  <pre id="res"></pre>
</div>
<script>
async function refresh(){
  const a= await fetch('/api/mempool/info'); document.getElementById('info').textContent = JSON.stringify(await a.json(),null,2);
  const b= await fetch('/api/mempool/txs'); document.getElementById('txs').textContent = JSON.stringify(await b.json(),null,2);
}
async function submit(){
  const r= await fetch('/api/mempool/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hex:document.getElementById('hex').value})});
  document.getElementById('res').textContent = JSON.stringify(await r.json(),null,2);
  await refresh();
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""

    def _get_tx_html(self) -> str:
        return """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Transactions</title>
<style>
  body { font-family: monospace; margin: 20px; background: #0a0a0a; color: #00ff00; }
  .card { background: #1a1a1a; border: 1px solid #00ff00; border-radius: 6px; padding: 16px; margin: 12px 0; }
  pre { white-space: pre-wrap; word-break: break-word; }
</style></head>
<body>
<h1>Transactions</h1>
<div class="card"><pre id="txs">Loading...</pre></div>
<script>
async function refresh(){
  const r= await fetch('/api/transactions'); document.getElementById('txs').textContent = JSON.stringify(await r.json(),null,2);
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""

    def _get_login_html(self, authed: bool) -> str:
        if not self.require_auth:
            return """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Login</title></head>
<body>
  <h2>Dashboard auth is disabled</h2>
  <p><a href="/">Go back</a></p>
</body></html>
"""

        if authed:
            return """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Login</title></head>
<body>
  <h2>Authenticated</h2>
  <p>Cookie set. Open <a href="/">dashboard</a>.</p>
</body></html>
"""

        return """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Login</title></head>
<body>
  <h2>Authentication required</h2>
  <p>Provide the token as an <code>X-Auth-Token</code> header for API calls, or open:</p>
  <pre>/login?token=YOUR_TOKEN</pre>
  <p>The token is printed in the node logs on startup when <code>web_require_auth = true</code>.</p>
</body></html>
"""

    # ========== API ==========

    async def api_auth_info(self, request: web.Request) -> web.Response:
        _ = request
        return json_response({"require_auth": bool(self.require_auth)})

    async def api_status(self, request: web.Request) -> web.Response:
        _ = request
        peers = len(self.node.connman.peers) if getattr(self.node, "connman", None) else 0
        return json_response(
            {
                "running": bool(getattr(self.node, "running", False)),
                "height": self.node.chainstate.get_best_height(),
                "peers": peers,
                "wallet_loaded": self.node.wallet is not None,
                "wallet_locked": self.node.wallet.locked if self.node.wallet else True,
                "mining": bool(getattr(self.node.miner, "is_mining", False)) if getattr(self.node, "miner", None) else False,
            }
        )

    async def api_blockchain(self, request: web.Request) -> web.Response:
        _ = request
        chain = self.node.chainstate
        tip = chain.get_best_block_hash() or ""
        best_h = chain.get_best_height()
        header = chain.get_header(best_h) if best_h >= 0 else None

        diff = 1.0
        if header:
            diff = ProofOfWork(chain.params).calculate_difficulty(header.bits)

        return json_response(
            {
                "height": best_h,
                "best_hash": tip,
                "difficulty": diff,
                "chainwork": str(chain.get_best_chainwork()),
                "peers": len(self.node.connman.peers) if getattr(self.node, "connman", None) else 0,
            }
        )

    async def api_peers(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "connman", None):
            return json_response({"peers": [], "static": []})
        peers = []
        for addr, peer in self.node.connman.peers.items():
            peers.append({
                "address": addr,
                "connected": bool(peer.connected),
                "outbound": bool(peer.is_outbound),
                "height": peer.peer_height if hasattr(peer, 'peer_height') else None,
            })
        static = list(self.node.connman.addrman.get_static_peers()) if getattr(self.node.connman, 'addrman', None) else []
        return json_response({"peers": peers, "static": static})

    async def api_peers_add(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "connman", None):
            return json_response({"error": "P2P not initialized"}, status=400)
        try:
            data = await request.json()
        except Exception:
            data = {}
        address = (data.get("address") or "").strip()
        if not address:
            return json_response({"error": "address required"}, status=400)
        # Add to addrman as static peer
        try:
            self.node.connman.addrman.add_static_peer(address)
        except Exception:
            pass

        # Try to connect immediately
        try:
            host, port = (address.split(":" ) + [str(self.node.config.get('port', 8333))])[:2]
            from node.p2p.peer import Peer

            peer = Peer(host, int(port), is_outbound=True)
            peer.on_message = self.node.connman.on_message
            peer.on_disconnect = self.node.connman._on_peer_disconnect
            ok = await peer.connect()
            if ok:
                # register with connman
                try:
                    self.node.connman._add_peer(peer)
                except Exception:
                    pass
                return json_response({"status": "connected", "address": peer.address})
            return json_response({"status": "failed", "address": address}, status=400)
        except Exception as e:
            return json_response({"error": str(e)}, status=500)

    def _wallet_addresses(self) -> list[str]:
        if not self.node.wallet:
            return []
        # Ensure addresses returned have no trailing whitespace/newlines
        return [addr.strip() for addr in self.node.wallet.keystore.keys.keys()]

    def _wallet_balance_sats(self) -> int:
        if not self.node.wallet:
            return 0
        addrs = self._wallet_addresses()
        if not addrs:
            return 0
        return sum(self.node.chainstate.get_balance(a) for a in addrs)

    async def api_wallet_info(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        w = self.node.wallet
        if w.locked:
            return json_response(
                {
                    "locked": True,
                    "balance": 0,
                    "address_count": len(w.keystore.keys),
                    "utxo_count": 0,
                }
            )

        # Wallet core in this repo doesn't keep balances updated, so compute from chainstate UTXO store.
        bal_sats = self._wallet_balance_sats()
        utxo_count = 0
        for a in self._wallet_addresses():
            utxo_count += len(self.node.chainstate.utxo_store.get_utxos_for_address(a, limit=10_000))

        return json_response(
            {
                "locked": False,
                "balance": bal_sats / 100_000_000,
                "address_count": len(w.keystore.keys),
                "utxo_count": utxo_count,
            }
        )

    async def api_wallet_keys(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        if self.node.wallet.locked:
            # Avoid exposing private keys; public metadata is still useful.
            keys = [
                {"address": addr.strip(), "used": ki.used, "path": ki.path, "public_key": ki.public_key.to_bytes().hex()}
                for addr, ki in self.node.wallet.keystore.keys.items()
            ]
            return json_response({"keys": keys})

        keys = [
            {
                "address": addr.strip(),
                "used": ki.used,
                "path": ki.path,
                "public_key": ki.public_key.to_bytes().hex(),
                # Private key string is optional; wallet signing in this repo uses this keystore directly.
                "private_key": ki.private_key.to_hex(),
            }
            for addr, ki in self.node.wallet.keystore.keys.items()
        ]
        return json_response({"keys": keys})

    async def api_wallet_create(self, request: web.Request) -> web.Response:
        """Create a new encrypted wallet file and attach it to the running node."""
        data = await request.json()
        password = (data.get("password") or "").strip()
        wallet_name = (data.get("wallet_name") or "main_wallet").strip() or "main_wallet"
        private_key_hex = (data.get("private_key") or "").strip()

        if not password:
            return json_response({"error": "Password required"}, status=400)

        from node.wallet.core.wallet import Wallet

        wallet_dir = self.node.config.get_datadir() / "wallets"
        wallet_dir.mkdir(parents=True, exist_ok=True)
        wallet_path = wallet_dir / wallet_name

        if wallet_path.exists():
            return json_response(
                {"error": "Wallet already exists. Use /api/wallet/load instead."},
                status=400,
            )

        wallet = Wallet(str(wallet_path), self.node.config.get("network"))
        mnemonic = wallet.create(password)

        if private_key_hex:
            try:
                addr = wallet.keystore.import_private_key(private_key_hex, label="Imported")
                if not addr:
                    return json_response({"error": "Failed to import private key"}, status=400)
            except Exception as e:
                return json_response({"error": f"Invalid private key: {e}"}, status=400)

        # Wallet starts locked; user unlocks explicitly.
        wallet.locked = True
        self.node.wallet = wallet

        return json_response(
            {
                "status": "created",
                "wallet_name": wallet_name,
                "wallet_path": str(wallet_path),
                "mnemonic": mnemonic,
                "warning": "Save your mnemonic and password safely.",
            }
        )

    async def api_wallet_load(self, request: web.Request) -> web.Response:
        """Load an existing encrypted wallet file and attach it to the running node."""
        data = await request.json()
        password = (data.get("password") or "").strip()
        wallet_name = (data.get("wallet_name") or "main_wallet").strip() or "main_wallet"

        if not password:
            return json_response({"error": "Password required"}, status=400)

        from node.wallet.core.wallet import Wallet

        wallet_dir = self.node.config.get_datadir() / "wallets"
        wallet_path = wallet_dir / wallet_name
        if not wallet_path.exists():
            return json_response({"error": f"Wallet not found: {wallet_name}"}, status=404)

        wallet = Wallet(str(wallet_path), self.node.config.get("network"))
        if not wallet.load(password):
            return json_response({"error": "Invalid password"}, status=401)

        wallet.locked = True  # keep locked until user unlocks
        self.node.wallet = wallet

        return json_response(
            {
                "status": "loaded",
                "wallet_name": wallet_name,
                "address_count": len(wallet.keystore.keys),
            }
        )

    async def api_wallet_unlock(self, request: web.Request) -> web.Response:
        data = await request.json()
        password = (data.get("password") or "").strip()
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        if not password:
            return json_response({"error": "password required"}, status=400)

        # Try the provided password first
        if self.node.wallet.unlock(password):
            return json_response({"status": "unlocked"})

        # Fall back to node config walletpassphrase (trimmed) for convenience
        cfg_pw = (self.node.config.get("walletpassphrase") or "").strip()
        if cfg_pw and cfg_pw != password and self.node.wallet.unlock(cfg_pw):
            logger.info("Wallet unlocked using config walletpassphrase fallback")
            return json_response({"status": "unlocked", "note": "unlocked with node config passphrase"})

        return json_response({"error": "invalid password"}, status=401)

    async def api_wallet_import_key(self, request: web.Request) -> web.Response:
        """Import a raw private key (hex) into the keystore without changing lock state."""
        data = await request.json()
        priv = (data.get("private_key") or "").strip()
        label = (data.get("label") or "").strip()
        if not priv:
            return json_response({"error": "private_key required"}, status=400)
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        try:
            addr = self.node.wallet.keystore.import_private_key(priv, label=label)
        except Exception as e:
            return json_response({"error": f"import failed: {e}"}, status=400)
        if not addr:
            return json_response({"error": "import failed or already exists"}, status=400)
        # Persist wallet state (best-effort)
        try:
            self.node.wallet._save()
        except Exception:
            pass
        return json_response({"address": addr, "status": "imported"})

    async def api_wallet_unlock_key(self, request: web.Request) -> web.Response:
        """Convenience: import a private key and unlock the wallet for signing.

        Note: This is for regtest/dev use only. Importing a key will persist it
        to the wallet file if the wallet has an encryption password set.
        """
        data = await request.json()
        priv = (data.get("private_key") or "").strip()
        label = (data.get("label") or "").strip()
        if not priv:
            return json_response({"error": "private_key required"}, status=400)
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        addr = self.node.wallet.keystore.import_private_key(priv, label=label)
        if not addr:
            return json_response({"error": "import failed or already exists"}, status=400)
        # Persist and unlock
        try:
            self.node.wallet._save()
        except Exception:
            pass
        # Mark wallet unlocked for immediate use
        self.node.wallet.locked = False
        logger.info("Wallet unlocked via imported private key %s", addr[:16])
        return json_response({"address": addr, "status": "unlocked"})

    async def api_wallet_lock(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        self.node.wallet.lock()
        return json_response({"status": "locked"})

    async def api_create_address(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        if self.node.wallet.locked:
            return json_response({"error": "Wallet locked"}, status=401)
        addr = self.node.wallet.get_new_address()
        if not addr:
            return json_response({"error": "Failed to create address"}, status=400)
        addr = addr.strip()
        ki = self.node.wallet.keystore.get_key(addr)
        return json_response(
            {
                "address": addr,
                "public_key": ki.public_key.to_bytes().hex() if ki else None,
                "private_key": ki.private_key.to_hex() if (ki and not self.node.wallet.locked) else None,
                "path": ki.path if ki else None,
            }
        )

    async def api_send_transaction(self, request: web.Request) -> web.Response:
        data = await request.json()
        to = (data.get("to") or "").strip()
        amount = data.get("amount")
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        if self.node.wallet.locked:
            return json_response({"error": "Wallet locked"}, status=401)
        if not to or amount is None:
            return json_response({"error": "to and amount required"}, status=400)
        satoshis = int(float(amount) * 100_000_000)
        try:
            txid = await self.node.wallet.send_to_address(to, satoshis)
        except Exception as e:
            # If wallet send failed, surface mempool rejection reason when available
            reason = getattr(self.node.mempool, 'last_reject_reason', None) if getattr(self.node, 'mempool', None) else None
            return json_response({"error": "send failed", "exception": str(e), "mempool_reason": reason}, status=400)
        if not txid:
            reason = getattr(self.node.mempool, 'last_reject_reason', None) if getattr(self.node, 'mempool', None) else None
            return json_response({"error": "send failed", "mempool_reason": reason}, status=400)
        return json_response({"txid": txid})

    async def api_wallet_balance(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        if self.node.wallet.locked:
            return json_response({"balance": 0, "satoshis": 0})
        bal_sats = self._wallet_balance_sats()
        return json_response({"balance": bal_sats / 100_000_000, "satoshis": bal_sats})

    async def api_wallet_utxos(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return json_response({"error": "No wallet"}, status=400)
        utxos = []
        best_h = self.node.chainstate.get_best_height()
        for a in self._wallet_addresses():
            for u in self.node.chainstate.utxo_store.get_utxos_for_address(a, limit=10_000):
                utxos.append(
                    {
                        "txid": u["txid"],
                        "vout": u["index"],
                        "address": a,
                        "amount": u["value"] / 100_000_000,
                        "satoshis": u["value"],
                        "height": u["height"],
                        "confirmations": max(0, best_h - u["height"] + 1),
                    }
                )
        return json_response({"utxos": utxos})

    async def api_mining_info(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "miner", None):
            return json_response({"error": "Miner not initialized"}, status=400)
        stats = self.node.miner.get_stats()
        best_height = self.node.chainstate.get_best_height()
        header = self.node.chainstate.get_header(best_height) if best_height >= 0 else None
        diff = 1.0
        if header:
            diff = ProofOfWork(self.node.chainstate.params).calculate_difficulty(header.bits)
        return json_response(
            {
                "mining_active": bool(stats.get("mining")),
                "blocks_mined": stats.get("blocks_mined", 0),
                "hashrate": stats.get("avg_hashrate", 0.0),
                "mining_address": self.node.config.get("miningaddress"),
                "current_height": best_height,
                "network_difficulty": diff,
                "uptime": stats.get("uptime", 0.0),
            }
        )

    async def api_mining_start(self, request: web.Request) -> web.Response:
        # Be tolerant: allow POST with empty body.
        try:
            data = await request.json()
        except Exception:
            data = {}
        address = data.get("address") or self.node.config.get("miningaddress")
        threads = int(data.get("threads", 1))
        if not getattr(self.node, "miner", None):
            return json_response({"error": "Miner not initialized"}, status=400)
        if self.node.config.get("network") != "regtest":
            return json_response({"error": "Dashboard mining controls are regtest only"}, status=400)
        if self.node.wallet and self.node.wallet.locked:
            return json_response({"error": "Wallet must be unlocked for mining"}, status=400)
        if not address:
            return json_response({"error": "Set mining address first"}, status=400)
        await self.node.miner.start_mining(address, threads=threads)
        return json_response({"status": "started", "threads": threads, "address": address})

    async def api_mining_stop(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "miner", None):
            return json_response({"error": "Miner not initialized"}, status=400)
        await self.node.miner.stop_mining()
        return json_response({"status": "stopped"})

    async def api_mining_generate(self, request: web.Request) -> web.Response:
        """Generate blocks immediately (regtest only) by invoking the internal RPC generate handler."""
        if self.node.config.get("network") != "regtest":
            return json_response({"error": "regtest only"}, status=400)
        try:
            data = await request.json()
        except Exception:
            data = {}
        num = int(data.get("num", 1))
        address = (data.get("address") or self.node.config.get("miningaddress") or "").strip()
        if not address:
            return json_response({"error": "address required"}, status=400)

        # Call internal RPC handler if available
        rpc = getattr(self.node, "rpc_server", None)
        if not rpc or "generate" not in rpc.handlers:
            return json_response({"error": "RPC generate not available"}, status=500)

        try:
            # Handler expects (num_blocks, address)
            handler = rpc.handlers["generate"]
            result = await handler(num, address)
            return json_response({"generated": result})
        except Exception as e:
            return json_response({"error": str(e)}, status=500)

    async def api_mining_address(self, request: web.Request) -> web.Response:
        _ = request
        addr = self.node.config.get("miningaddress") or ""
        return json_response({"address": addr.strip()})

    async def api_set_mining_address(self, request: web.Request) -> web.Response:
        data = await request.json()
        address = (data.get("address") or "").strip()
        if not address:
            return json_response({"error": "address required"}, status=400)
        # Store the cleaned address
        self.node.config.set("miningaddress", address)
        if getattr(self.node, "miner", None):
            self.node.miner.mining_address = address
        return json_response({"address": address, "status": "updated"})

    async def api_mempool_info(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "mempool", None):
            return json_response({"error": "Mempool not initialized"}, status=400)
        try:
            stats = await self.node.mempool.get_stats()
        except Exception as e:
            logger.warning("Failed to get mempool stats: %s", e)
            # Return minimal empty stats to avoid crashing the UI
            return json_response(
                {
                    "size": 0,
                    "bytes": 0,
                    "weight": 0,
                    "fee_total": 0,
                    "min_fee_rate": 0,
                    "max_fee_rate": 0,
                }
            )
        return json_response(
            {
                "size": stats["size"],
                "bytes": stats["total_size"],
                "weight": stats["total_weight"],
                "fee_total": stats["total_fee"],
                "min_fee_rate": stats["min_fee_rate"],
                "max_fee_rate": stats["max_fee_rate"],
            }
        )

    async def api_mempool_txs(self, request: web.Request) -> web.Response:
        _ = request
        if not getattr(self.node, "mempool", None):
            return json_response({"error": "Mempool not initialized"}, status=400)
        txs = await self.node.mempool.get_transactions(200)
        out = []
        for tx in txs:
            tid = tx.txid().hex()
            entry = self.node.mempool.transactions.get(tid) if self.node.mempool else None
            out.append(
                {
                    "txid": tid,
                    "inputs": len(tx.vin),
                    "outputs": len(tx.vout),
                    "size": len(tx.serialize()),
                    "fee": entry.fee if entry else None,
                }
            )
        return json_response({"transactions": out})

    async def api_submit_transaction(self, request: web.Request) -> web.Response:
        data = await request.json()
        hex_string = data.get("hex")
        if not hex_string:
            return json_response({"error": "hex required"}, status=400)
        from shared.core.transaction import Transaction

        tx_bytes = bytes.fromhex(hex_string.strip())
        tx, _ = Transaction.deserialize(tx_bytes)
        ok = await self.node.mempool.add_transaction(tx)
        if not ok:
            reason = getattr(self.node.mempool, 'last_reject_reason', None)
            return json_response({"error": "Transaction rejected", "reason": reason}, status=400)
        return json_response({"txid": tx.txid().hex(), "status": "accepted"})

    async def api_transactions(self, request: web.Request) -> web.Response:
        _ = request
        chain = self.node.chainstate
        best_h = chain.get_best_height()
        if best_h < 0:
            return json_response({"transactions": []})
        txs = []
        for height in range(max(0, best_h - 100), best_h + 1):
            blk = chain.get_block_by_height(height)
            if not blk:
                continue
            for tx in blk.transactions:
                txs.append(
                    {
                        "txid": tx.txid().hex(),
                        "height": height,
                        "inputs": len(tx.vin),
                        "outputs": len(tx.vout),
                        "is_coinbase": tx.is_coinbase(),
                    }
                )
        return json_response({"transactions": txs[-200:]})

