"""Web dashboard for BerzCoin (optional; aiohttp)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger

logger = get_logger()

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _WEB_DIR / "static"


class WebDashboard:
    """Minimal HTML + JSON API for node status (local control panel)."""

    def __init__(
        self,
        node: Any,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        self.app = web.Application()
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/api/status", self.api_status)
        self.app.router.add_get("/api/blockchain", self.api_blockchain)
        self.app.router.add_get("/api/mempool", self.api_mempool)
        self.app.router.add_get("/api/wallet", self.api_wallet)
        self.app.router.add_get("/api/wallet/newaddress", self.api_wallet_new_address)
        self.app.router.add_get("/api/mining", self.api_mining)
        self.app.router.add_post("/api/send", self.api_send)
        self.app.router.add_post("/api/mine", self.api_mine)

        if _STATIC_DIR.is_dir():
            self.app.router.add_static("/static", _STATIC_DIR)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info("Web dashboard at http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        self.app = None

    async def index(self, request: web.Request) -> web.StreamResponse:
        _ = request
        html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>BerzCoin Dashboard</title>
<style>
body { font-family: monospace; margin: 20px; background: #1a1a1a; color: #00ff00; }
.container { max-width: 1200px; margin: 0 auto; }
.card { background: #2a2a2a; padding: 15px; margin: 10px 0; border-radius: 5px; }
button { background: #00ff00; color: #1a1a1a; padding: 10px; margin: 5px; border: none; cursor: pointer; }
button:hover { background: #00cc00; }
input { padding: 8px; margin: 5px; }
</style>
</head>
<body>
<div class="container">
<h1>BerzCoin Dashboard</h1>
<div class="card"><h2>Blockchain</h2><div id="blockchain">Loading...</div></div>
<div class="card"><h2>Wallet</h2><div id="wallet">Loading...</div>
<button type="button" onclick="getNewAddress()">New address</button>
<div id="newAddress"></div></div>
<div class="card"><h2>Send</h2>
<input type="text" id="sendAddress" placeholder="Address">
<input type="number" id="sendAmount" placeholder="Amount (BERZ)" step="any">
<button type="button" onclick="sendTransaction()">Send</button>
<div id="sendResult"></div></div>
<div class="card"><h2>Mining</h2><div id="mining">Loading...</div>
<button type="button" onclick="startMining()">Start</button>
<button type="button" onclick="stopMining()">Stop</button></div>
</div>
<script>
async function refresh() {
  await loadBlockchain();
  await loadWallet();
  await loadMining();
}
async function loadBlockchain() {
  const resp = await fetch('/api/blockchain');
  const data = await resp.json();
  const bh = data.best_hash ? String(data.best_hash) : '';
  document.getElementById('blockchain').innerHTML =
    'Height: ' + data.height + '<br>Best hash: ' + (bh ? bh.substring(0, 32) + '...' : 'n/a') +
    '<br>Difficulty: ' + data.difficulty + '<br>Peers: ' + data.peers;
}
async function loadWallet() {
  const resp = await fetch('/api/wallet');
  const data = await resp.json();
  if (data.error) { document.getElementById('wallet').textContent = data.error; return; }
  let lockedNote = data.locked ? '<br><em>Wallet locked — unlock via CLI to spend.</em>' : '';
  document.getElementById('wallet').innerHTML =
    'Balance: ' + data.balance + ' BERZ' + lockedNote +
    '<br>Addresses: ' + data.address_count + '<br>UTXOs: ' + data.utxo_count;
}
async function loadMining() {
  const resp = await fetch('/api/mining');
  const data = await resp.json();
  if (data.error) {
    document.getElementById('mining').textContent = data.error;
    return;
  }
  const hr = typeof data.hashrate === 'number' ? data.hashrate.toFixed(2) : '0';
  document.getElementById('mining').innerHTML =
    'Mining: ' + (data.mining ? 'Running' : 'Stopped') + '<br>Blocks: ' + data.blocks_mined + '<br>Hashrate: ' + hr + ' H/s';
}
async function getNewAddress() {
  const resp = await fetch('/api/wallet/newaddress');
  const data = await resp.json();
  document.getElementById('newAddress').textContent = data.error ? data.error : ('New: ' + data.new_address);
}
async function sendTransaction() {
  const address = document.getElementById('sendAddress').value;
  const amount = document.getElementById('sendAmount').value;
  const resp = await fetch('/api/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({address, amount: parseFloat(amount)})
  });
  const data = await resp.json();
  document.getElementById('sendResult').textContent = data.error ? data.error : ('TXID: ' + data.txid);
  refresh();
}
async function startMining() {
  await fetch('/api/mine', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'start'})});
  refresh();
}
async function stopMining() {
  await fetch('/api/mine', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'stop'})});
  refresh();
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def api_status(self, request: web.Request) -> web.Response:
        _ = request
        uptime = 0.0
        if getattr(self.node, "start_time", None):
            uptime = time.time() - float(self.node.start_time)
        peers = 0
        if self.node.connman:
            peers = len(self.node.connman.peers)
        return web.json_response(
            {
                "running": bool(getattr(self.node, "running", False)),
                "height": self.node.chainstate.get_best_height(),
                "peers": peers,
                "uptime": uptime,
            }
        )

    def _tip_header(self) -> Any:
        h = self.node.chainstate.get_best_height()
        if h < 0:
            return None
        return self.node.chainstate.get_header(h)

    async def api_blockchain(self, request: web.Request) -> web.Response:
        _ = request
        chain = self.node.chainstate
        best_hash = chain.get_best_block_hash() or ""
        tip = self._tip_header()
        diff = self._get_difficulty(tip.bits) if tip else 1.0
        peers = len(self.node.connman.peers) if self.node.connman else 0
        return web.json_response(
            {
                "height": chain.get_best_height(),
                "best_hash": best_hash,
                "difficulty": diff,
                "peers": peers,
            }
        )

    async def api_mempool(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.mempool:
            return web.json_response({"error": "No mempool"})
        stats = await self.node.mempool.get_stats()
        return web.json_response(
            {
                "size": stats["size"],
                "bytes": stats["total_size"],
                "fees": stats["total_fee"],
            }
        )

    async def api_wallet(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return web.json_response({"error": "No wallet"})
        w = self.node.wallet
        if w.locked:
            return web.json_response(
                {
                    "balance": 0,
                    "address_count": len(w.keystore.keys),
                    "utxo_count": 0,
                    "locked": True,
                }
            )

        addresses = list(w.keystore.keys.keys())
        bal_sats = sum(self.node.chainstate.get_balance(a) for a in addresses)
        # Small demo workloads only; limit is plenty for 101 mined blocks.
        utxo_count = 0
        for a in addresses:
            utxo_count += len(self.node.chainstate.utxo_store.get_utxos_for_address(a, limit=10_000))

        return web.json_response(
            {
                "balance": bal_sats / 100_000_000,
                "address_count": len(w.keystore.keys),
                "utxo_count": utxo_count,
                "locked": False,
            }
        )

    async def api_wallet_new_address(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.wallet:
            return web.json_response({"error": "No wallet"})
        addr = self.node.wallet.get_new_address()
        if not addr:
            return web.json_response({"error": "Could not create address (locked?)"})
        return web.json_response({"new_address": addr})

    async def api_mining(self, request: web.Request) -> web.Response:
        _ = request
        if not self.node.miner:
            return web.json_response({"error": "No miner"})
        stats = self.node.miner.get_stats()
        return web.json_response(
            {
                "mining": stats["mining"],
                "blocks_mined": stats["blocks_mined"],
                "hashrate": stats["avg_hashrate"],
            }
        )

    async def api_send(self, request: web.Request) -> web.Response:
        data = await request.json()
        address = data.get("address")
        amount = float(data.get("amount", 0))
        if not self.node.wallet:
            return web.json_response({"error": "No wallet"}, status=400)
        if self.node.wallet.locked:
            return web.json_response(
                {"error": "Wallet is locked — unlock first (same password as wallet)."},
                status=400,
            )
        if not address:
            return web.json_response({"error": "Missing address"}, status=400)
        if amount <= 0:
            return web.json_response({"error": "Amount must be positive"}, status=400)
        satoshis = int(amount * 100_000_000)
        txid = self.node.wallet.send_to_address(address, satoshis)
        if not txid:
            return web.json_response(
                {
                    "error": "Send failed — need coins first (mine on regtest, then spend UTXOs).",
                },
                status=400,
            )
        return web.json_response({"txid": txid})

    async def api_mine(self, request: web.Request) -> web.Response:
        data = await request.json()
        action = data.get("action")
        if not self.node.miner:
            return web.json_response(
                {"error": "No miner (regtest/mainnet full node only)"},
                status=400,
            )
        if action == "start":
            wallet = getattr(self.node, "wallet", None)
            if wallet and wallet.locked:
                return web.json_response(
                    {"error": "Wallet must be unlocked for mining"},
                    status=400,
                )
            mining_addr = (self.node.config.get("miningaddress") or "").strip()
            if not mining_addr:
                return web.json_response(
                    {"error": "Set a mining/coinbase address first (config or RPC)"},
                    status=400,
                )
            if self.node.config.get("network") != "regtest":
                return web.json_response(
                    {"error": "Dashboard mining controls are for regtest"},
                    status=400,
                )
            await self.node.miner.start_mining(mining_addr)
            return web.json_response({"status": "started"})
        if action == "stop":
            await self.node.miner.stop_mining()
            return web.json_response({"status": "stopped"})
        return web.json_response({"error": "Invalid action"}, status=400)

    def _get_difficulty(self, bits: int) -> float:
        pow_check = ProofOfWork(self.node.chainstate.params)
        return pow_check.calculate_difficulty(bits)
