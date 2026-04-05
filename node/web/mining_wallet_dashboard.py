"""Mining and Wallet Dashboard - Private key based."""

import asyncio
import json
import time
from aiohttp import web
from aiohttp.web import json_response
from shared.utils.logging import get_logger
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
from node.app.interface_verifier import TwoNodeFlowVerifier
from node.wallet.simple_wallet import SimpleWalletManager
from node.wallet.core.tx_builder import TransactionBuilder

logger = get_logger()

class MiningWalletDashboard:
    """Dashboard for mining and wallet control."""
    
    def __init__(self, node, host="127.0.0.1", port=8080):
        """Initialize dashboard."""
        self.node = node
        self.host = host
        self.port = port
        self.app = None
        self.runner = None
        self._flow_running = False
        self._flow_task = None
        self._flow_started_at = 0
        self._flow_last_result = None
        self._flow_last_error = None

    def _wallet_manager(self) -> SimpleWalletManager:
        manager = getattr(self.node, "simple_wallet_manager", None)
        if manager is None:
            manager = SimpleWalletManager(
                self.node.config.get_datadir(),
                network=self.node.config.get("network", "mainnet"),
            )
            setattr(self.node, "simple_wallet_manager", manager)
        return manager

    def _allow_wallet_debug_secrets(self) -> bool:
        cfg = self.node.config
        if not bool(cfg.get("wallet_debug_secrets", False)):
            return False
        network = str(cfg.get("network", "mainnet") or "mainnet").strip().lower()
        is_dev_mode = bool(cfg.get("debug", False))
        return network == "regtest" or is_dev_mode

    def _wallet_public_payload(self, wallet, include_debug_secrets: bool = False) -> dict:
        balance_sats = int(self.node.chainstate.get_balance(wallet.address))
        manager = self._wallet_manager()
        payload = {
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "balance": balance_sats / 100000000,
            "watch_only": bool(getattr(wallet, "watch_only", False)),
            "unlocked": bool(manager.is_wallet_unlocked()),
            "unlocked_until": int(getattr(manager, "_unlocked_until", 0)),
            "debug_secrets_allowed": False,
        }
        if include_debug_secrets and self._allow_wallet_debug_secrets():
            payload["private_key"] = wallet.private_key_hex
            payload["mnemonic"] = wallet.mnemonic
            payload["debug_secrets_allowed"] = True
        return payload
    
    async def start(self):
        """Start dashboard."""
        self.app = web.Application()
        
        # HTML routes
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/wallet', self.wallet_page)
        self.app.router.add_get('/mining', self.mining_page)
        self.app.router.add_get('/blocks', self.blocks_page)
        self.app.router.add_get('/mempool', self.mempool_page)
        self.app.router.add_get('/network', self.network_page)
        
        # API routes
        self.app.router.add_post('/api/wallet/activate', self.activate_wallet)
        self.app.router.add_get('/api/wallet/info', self.wallet_info)
        self.app.router.add_post('/api/wallet/create', self.create_wallet)
        self.app.router.add_post('/api/wallet/unlock', self.wallet_unlock)
        self.app.router.add_post('/api/wallet/lock', self.wallet_lock)
        self.app.router.add_post('/api/wallet/send', self.send_transaction)
        self.app.router.add_get('/api/wallet/balance', self.get_balance)
        
        self.app.router.add_post('/api/mining/start', self.start_mining)
        self.app.router.add_post('/api/mining/stop', self.stop_mining)
        self.app.router.add_get('/api/mining/info', self.mining_info)
        self.app.router.add_post('/api/mining/address', self.set_mining_address)
        
        self.app.router.add_get('/api/blockchain', self.blockchain_info)
        self.app.router.add_get('/api/dashboard/summary', self.dashboard_summary)
        self.app.router.add_get('/api/blocks/recent', self.recent_blocks)
        self.app.router.add_get('/api/mempool/entries', self.mempool_entries)
        self.app.router.add_get('/api/network/peers', self.network_peers)
        self.app.router.add_get('/api/authority/chain', self.authority_chain_info)
        self.app.router.add_post('/api/interface/verify-two-node-flow', self.verify_two_node_flow)
        self.app.router.add_get('/api/interface/verify-two-node-flow', self.verify_two_node_flow_status)
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        
        logger.info(f"Mining/Wallet Dashboard started on http://{self.host}:{self.port}")

    async def stop(self):
        """Stop dashboard server."""
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.app = None
    
    async def index(self, request):
        """Main dashboard page."""
        return web.Response(text=self._get_main_html(), content_type='text/html')
    
    async def wallet_page(self, request):
        """Wallet management page."""
        return web.Response(text=self._get_wallet_html(), content_type='text/html')
    
    async def mining_page(self, request):
        """Mining control page."""
        return web.Response(text=self._get_mining_html(), content_type='text/html')

    async def blocks_page(self, request):
        """Blocks explorer page."""
        return web.Response(text=self._get_blocks_html(), content_type='text/html')

    async def mempool_page(self, request):
        """Mempool page."""
        return web.Response(text=self._get_mempool_html(), content_type='text/html')

    async def network_page(self, request):
        """Network and peers page."""
        return web.Response(text=self._get_network_html(), content_type='text/html')
    
    
    # ========== Wallet API ==========
    
    async def create_wallet(self, request):
        """Create and activate a new private-key wallet."""
        wallet = self._wallet_manager().create_wallet()
        self._wallet_manager().active_wallet = wallet
        self._wallet_manager().active_private_key = wallet.private_key_hex
        payload = self._wallet_public_payload(wallet, include_debug_secrets=True)
        payload["warning"] = (
            "Wallet created. Secrets are hidden by default; use secure backup/export flow."
        )
        return json_response(payload)
    
    async def activate_wallet(self, request):
        """Activate wallet with private key."""
        data = await request.json()
        private_key = str(data.get('private_key', '') or '').strip()
        if not private_key:
            return json_response({'error': 'Private key required'}, status=400)
        try:
            wallet = self._wallet_manager().activate_wallet(private_key)
        except Exception:
            return json_response({'error': 'Invalid private key'}, status=400)
        if wallet is None:
            return json_response({'error': 'Invalid private key'}, status=400)
        payload = self._wallet_public_payload(wallet, include_debug_secrets=True)
        payload["status"] = "activated"
        return json_response(payload)
    
    async def wallet_info(self, request):
        """Get current wallet info."""
        wallet = self._wallet_manager().get_active_wallet()
        if not wallet:
            return json_response({'active': False})
        payload = self._wallet_public_payload(wallet, include_debug_secrets=True)
        payload["active"] = True
        return json_response(payload)

    async def wallet_unlock(self, request):
        """Unlock active wallet for signing for timeout seconds."""
        data = await request.json()
        passphrase = str(data.get("passphrase", "") or "")
        timeout = int(data.get("timeout", 300) or 300)
        manager = self._wallet_manager()
        if manager.get_active_wallet() is None:
            return json_response({"error": "No active wallet"}, status=400)
        if not manager.wallet_passphrase(passphrase, timeout):
            return json_response({"error": "Invalid passphrase or timeout"}, status=400)
        return json_response(
            {
                "status": "unlocked",
                "timeout": int(timeout),
                "unlocked_until": int(getattr(manager, "_unlocked_until", 0)),
            }
        )

    async def wallet_lock(self, request):
        """Lock active wallet immediately."""
        manager = self._wallet_manager()
        manager.lock_wallet()
        return json_response({"status": "locked"})
    
    async def get_balance(self, request):
        """Get wallet balance."""
        wallet = self._wallet_manager().get_active_wallet()
        if not wallet:
            return json_response({'balance': 0, 'satoshis': 0})
        balance = self.node.chainstate.get_balance(wallet.address)
        return json_response({'balance': balance / 100000000, 'satoshis': balance})
    
    async def send_transaction(self, request):
        """Send transaction."""
        if not self.node.mempool:
            return json_response({'error': 'Mempool unavailable'}, status=500)
        data = await request.json()
        to_address = data.get('to')
        amount = float(data.get('amount', 0))
        private_key = str(data.get('private_key', '') or '').strip()

        if not to_address or amount <= 0:
            return json_response({'error': 'Invalid parameters'}, status=400)

        satoshis = int(amount * 100000000)

        manager = self._wallet_manager()
        if private_key:
            try:
                wallet = manager.activate_wallet(private_key)
            except Exception:
                wallet = None
        else:
            wallet = manager.get_active_wallet()
        if not wallet:
            return json_response({'error': 'No active wallet. Provide private key.'}, status=400)

        from_address = wallet.address
        utxos = self.node.chainstate.get_utxos_for_address(from_address, 1000)
        if not utxos:
            return json_response({'error': 'No UTXOs found'}, status=400)
        best_height = int(self.node.chainstate.get_best_height())
        maturity = int(getattr(self.node.chainstate.params, "coinbase_maturity", 100))
        spendable_utxos = []
        immature_sats = 0
        for utxo in utxos:
            if bool(utxo.get("is_coinbase", False)):
                utxo_height = int(utxo.get("height", 0) or 0)
                confirmations = best_height - utxo_height + 1 if utxo_height > 0 else 0
                if confirmations < maturity:
                    immature_sats += int(utxo.get("value", 0))
                    continue
            spendable_utxos.append(utxo)
        if not spendable_utxos:
            return json_response(
                {
                    'error': (
                        f'No spendable UTXOs yet. Coinbase rewards need {maturity} confirmations '
                        f'({immature_sats / 100000000:.8f} BERZ currently immature).'
                    )
                },
                status=400,
            )

        selected = []
        selected_amount = 0
        mempool_policy = getattr(self.node.mempool, "policy", None)
        min_relay_fee = int(getattr(mempool_policy, "min_relay_fee", 1))
        # Baseline fee estimate kept compatible with existing tx builder behavior.
        target_fee = max(10 + 150 + 34, min_relay_fee)
        for utxo in spendable_utxos:
            selected.append(utxo)
            selected_amount += int(utxo.get("value", 0))
            if selected_amount >= satoshis + target_fee:
                break
        if selected_amount < satoshis + target_fee:
            extra = ""
            if immature_sats > 0:
                extra = f" ({immature_sats / 100000000:.8f} BERZ is immature coinbase)"
            return json_response({'error': f'Insufficient spendable funds{extra}'}, status=400)

        builder = TransactionBuilder(self.node.config.get("network", "mainnet"))
        inputs = [(u['txid'], int(u['index']), int(u['value'])) for u in selected]
        outputs = [(to_address, satoshis)]
        tx = builder.create_transaction(inputs, outputs, from_address, fee=target_fee)

        signing_key_hex = manager.get_active_private_key()
        if private_key:
            signing_key_hex = private_key
        if not signing_key_hex:
            return json_response(
                {
                    "error": "Wallet is locked. Use wallet unlock first or provide private key for activation."
                },
                status=400,
            )
        try:
            private_key_obj = PrivateKey(int(signing_key_hex, 16))
        except ValueError:
            return json_response({'error': 'Invalid active private key'}, status=400)

        pubkey = bytes.fromhex(wallet.public_key_hex)
        selected_map = {(str(u["txid"]), int(u["index"])): u for u in selected}

        def _sign_transaction(candidate_tx):
            for idx, txin in enumerate(candidate_tx.vin):
                outpoint = (txin.prev_tx_hash.hex(), int(txin.prev_tx_index))
                utxo = selected_map.get(outpoint)
                if not utxo or "script_pubkey" not in utxo:
                    utxo = self.node.chainstate.get_utxo(*outpoint)
                if not utxo:
                    raise ValueError(f"Missing UTXO for input {idx}")
                script_pubkey = utxo.get("script_pubkey", b"")
                if not isinstance(script_pubkey, (bytes, bytearray)):
                    script_pubkey = bytes(script_pubkey)
                sighash = calculate_legacy_sighash(candidate_tx, idx, SIGHASH_ALL, bytes(script_pubkey))
                signature = sign_message_hash(private_key_obj, sighash) + bytes([SIGHASH_ALL])
                txin.script_sig = (
                    bytes([len(signature)]) + signature + bytes([len(pubkey)]) + pubkey
                )

        try:
            _sign_transaction(tx)
        except ValueError as e:
            return json_response({'error': str(e)}, status=400)

        # If mempool policy is available, enforce fee floor against signed tx size.
        if mempool_policy is not None:
            required_fee = max(min_relay_fee * tx.size(), min_relay_fee)
            current_fee = selected_amount - sum(out.value for out in tx.vout)
            if required_fee > current_fee:
                if selected_amount < satoshis + required_fee:
                    return json_response({'error': 'Insufficient funds'}, status=400)
                tx = builder.create_transaction(inputs, outputs, from_address, fee=required_fee)
                try:
                    _sign_transaction(tx)
                except ValueError as e:
                    return json_response({'error': str(e)}, status=400)

        if hasattr(self.node, "on_transaction"):
            accepted, _txid, reason = await self.node.on_transaction(tx, relay=True)
            if not accepted:
                return json_response({'error': f'Transaction rejected: {reason}'}, status=400)
        else:
            accepted = await self.node.mempool.add_transaction(tx)
            if not accepted:
                return json_response({'error': 'Transaction rejected'}, status=400)

        return json_response({
            'txid': tx.txid().hex(),
            'from': from_address,
            'to': to_address,
            'amount': amount
        })
    
    # ========== Mining API ==========
    
    async def start_mining(self, request):
        """Start mining."""
        try:
            data = await request.json()
        except Exception:
            data = {}
        requested_address = (data.get('address') or '').strip()
        mining_address = requested_address
        if not mining_address:
            mining_address = (self.node.miner.mining_address or '').strip()
        if not mining_address:
            manager = self._wallet_manager()
            active_wallet = manager.get_active_wallet()
            if active_wallet:
                mining_address = active_wallet.address
        if not mining_address:
            return json_response({'error': 'Set a mining address first (or activate a wallet)'}, status=400)
        try:
            TransactionBuilder(self.node.config.get("network", "mainnet"))._create_script_pubkey(mining_address)
        except Exception:
            return json_response({'error': 'Invalid mining address'}, status=400)
        self.node.miner.mining_address = mining_address
        self.node.config.set('miningaddress', mining_address)

        await self.node.miner.start_mining(mining_address=mining_address)
        return json_response({'status': 'started', 'address': mining_address})
    
    async def stop_mining(self, request):
        """Stop mining."""
        await self.node.miner.stop_mining()
        return json_response({'status': 'stopped'})
    
    async def mining_info(self, request):
        """Get mining info."""
        stats = self.node.miner.get_stats() if self.node.miner else {}
        return json_response({
            'mining_active': stats.get('mining_active', False),
            'blocks_mined': stats.get('blocks_mined', 0),
            'hashrate': stats.get('hashrate', 0),
            'mining_address': self.node.miner.mining_address if self.node.miner else '',
            'target_block_time': stats.get('target_block_time', 120),
            'last_reward_address': stats.get('last_reward_address', ''),
            'last_subsidy_sats': stats.get('last_subsidy_sats', 0),
            'last_fees_sats': stats.get('last_fees_sats', 0),
            'last_reward_sats': stats.get('last_reward_sats', 0),
            'last_stop_reason': stats.get('last_stop_reason', ''),
            'current_height': self.node.chainstate.get_best_height()
        })
    
    async def set_mining_address(self, request):
        """Set mining reward address (independent from currently active wallet)."""
        data = await request.json()
        address = (data.get('address') or '').strip()
        if not address:
            return json_response({'error': 'Address required'}, status=400)
        try:
            TransactionBuilder(self.node.config.get("network", "mainnet"))._create_script_pubkey(address)
        except Exception:
            return json_response({'error': 'Invalid mining address'}, status=400)

        self.node.miner.mining_address = address
        self.node.config.set('miningaddress', address)
        return json_response({'address': address})
    
    async def blockchain_info(self, request):
        """Get blockchain info."""
        chain = self.node.chainstate
        return json_response({
            'height': chain.get_best_height(),
            'best_hash': chain.get_best_block_hash(),
            'difficulty': self._get_difficulty(),
            'mining_target_time': int(chain.params.pow_target_spacing)
        })

    async def dashboard_summary(self, request):
        """Get high-level dashboard summary."""
        chain = self.node.chainstate
        wallet = self._wallet_manager().get_active_wallet()
        mining_stats = self.node.miner.get_stats() if self.node.miner else {}
        connman = getattr(self.node, "connman", None)
        peers = connman.get_connected_count() if connman else 0
        mempool_count = len(self.node.mempool.transactions) if self.node.mempool else 0

        return json_response({
            'wallet_active': bool(wallet),
            'wallet_address': wallet.address if wallet else '',
            'wallet_balance': (chain.get_balance(wallet.address) / 100000000) if wallet else 0,
            'height': chain.get_best_height(),
            'best_hash': chain.get_best_block_hash(),
            'difficulty': self._get_difficulty(),
            'mempool_count': mempool_count,
            'node_connected': peers > 0,
            'peers': peers,
            'mining_active': bool(mining_stats.get('mining_active', False)),
            'hashrate': float(mining_stats.get('hashrate', 0)),
            'blocks_mined': int(mining_stats.get('blocks_mined', 0)),
            'mining_address': self.node.miner.mining_address if self.node.miner else '',
        })

    async def recent_blocks(self, request):
        """Get recent blocks for explorer view."""
        chain = self.node.chainstate
        try:
            count = max(1, min(100, int(request.query.get("count", "20"))))
        except Exception:
            count = 20
        best = chain.get_best_height()
        rows = []
        for h in range(best, max(-1, best - count), -1):
            block = chain.get_block_by_height(h)
            if not block:
                continue
            reward = 0
            if block.transactions and block.transactions[0].vout:
                reward = sum(int(out.value) for out in block.transactions[0].vout)
            rows.append({
                'height': h,
                'hash': block.header.hash_hex(),
                'prev_hash': block.header.prev_block_hash.hex(),
                'timestamp': int(block.header.timestamp),
                'nonce': int(block.header.nonce),
                'bits': int(block.header.bits),
                'tx_count': len(block.transactions),
                'reward_sats': reward,
            })
        return json_response({'blocks': rows})

    async def mempool_entries(self, request):
        """Get mempool entries."""
        if not self.node.mempool:
            return json_response({'entries': [], 'count': 0})
        entries = []
        for txid, ent in self.node.mempool.transactions.items():
            entries.append({
                'txid': txid,
                'inputs': len(ent.tx.vin),
                'outputs': len(ent.tx.vout),
                'fee': int(ent.fee),
                'fee_rate': float(ent.fee_rate),
                'size': int(ent.size),
                'weight': int(ent.weight),
                'age_secs': max(0, int(time.time() - ent.time_added)),
            })
        entries.sort(key=lambda x: x['fee_rate'], reverse=True)
        return json_response({'entries': entries, 'count': len(entries)})

    async def network_peers(self, request):
        """Get peer/network info."""
        connman = getattr(self.node, "connman", None)
        if connman is None:
            return json_response({'connected': 0, 'peers': []})
        peers = []
        for peer in connman.peers.values():
            peers.append({
                'address': peer.address,
                'outbound': bool(peer.is_outbound),
                'connected': bool(peer.connected),
                'peer_height': int(peer.peer_height),
                'connected_secs': max(0, int(time.time() - float(getattr(peer, "connected_at", 0) or 0))),
            })
        return json_response({'connected': connman.get_connected_count(), 'peers': peers})

    async def authority_chain_info(self, request):
        """Get authority-chain admission state."""
        connman = getattr(self.node, "connman", None)
        if connman is None:
            return json_response({'enabled': False, 'error': 'P2P not initialized'}, status=503)
        enabled = bool(getattr(connman, "authority_chain_enabled", False))
        if not enabled:
            return json_response({'enabled': False, 'verified_nodes': [], 'verifiers': [], 'verified_by': {}})
        status = connman.authority_chain.get_status()
        status["enabled"] = True
        return json_response(status)

    async def verify_two_node_flow(self, request):
        """Run two-node end-to-end verification flow for interface users."""
        if self._flow_running:
            return json_response(await self._build_flow_status())
        self._flow_running = True
        self._flow_started_at = int(time.time())
        self._flow_last_result = None
        self._flow_last_error = None
        self._flow_task = asyncio.create_task(self._run_two_node_flow())
        return json_response(await self._build_flow_status())

    async def _run_two_node_flow(self) -> None:
        try:
            verifier = TwoNodeFlowVerifier()
            result = await asyncio.to_thread(verifier.run, 180)
            self._flow_last_result = result
        except Exception as e:
            self._flow_last_error = str(e)
            self._flow_last_result = {'ok': False, 'error': self._flow_last_error}
            logger.exception("Two-node verification flow crashed")
        finally:
            self._flow_running = False
            self._flow_task = None

    async def _build_flow_status(self):
        now = int(time.time())
        return {
            'running': bool(self._flow_running),
            'started_at': int(self._flow_started_at or 0),
            'elapsed_secs': max(0, now - int(self._flow_started_at or now)),
            'result': self._flow_last_result,
            'error': self._flow_last_error,
        }

    async def verify_two_node_flow_status(self, request):
        """Get latest two-node verification flow status/result."""
        return json_response(await self._build_flow_status())
    
    def _get_difficulty(self) -> float:
        """Get current difficulty."""
        if not self.node.chainstate:
            return 1.0

        best_height = self.node.chainstate.get_best_height()
        if best_height < 0:
            return 1.0
        best_header = self.node.chainstate.get_header(best_height)
        if not best_header:
            return 1.0
        
        from shared.consensus.pow import ProofOfWork
        pow_check = ProofOfWork(self.node.chainstate.params)
        
        target = pow_check.get_target(best_header.bits)
        max_target = self.node.chainstate.params.pow_limit
        
        return max_target / target
    
    def _get_main_html(self):
        """Get main dashboard HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>BerzCoin Mining & Wallet</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: monospace; background: #0a0a0a; color: #00ff00; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                h1 { color: #00ff00; border-bottom: 2px solid #00ff00; margin-bottom: 20px; }
                .nav { background: #1a1a1a; padding: 10px; margin-bottom: 20px; border: 1px solid #00ff00; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 15px; }
                .nav a:hover { background: #00ff00; color: #0a0a0a; padding: 5px; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 20px; margin: 10px 0; border-radius: 5px; }
                button { background: #00ff00; color: #0a0a0a; padding: 10px 20px; margin: 5px; border: none; cursor: pointer; font-family: monospace; font-weight: bold; }
                button:hover { background: #00cc00; }
                input, textarea { background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; margin: 5px; font-family: monospace; width: 100%; }
                .warning { color: #ffaa00; }
                .error { color: #ff0000; }
                .success { color: #00ff00; }
                pre { background: #0a0a0a; padding: 10px; overflow-x: auto; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>⛏️ BerzCoin Mining & Wallet</h1>
                
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/wallet">Wallet</a>
                    <a href="/mining">Mining</a>
                    <a href="/blocks">Blocks</a>
                    <a href="/mempool">Mempool</a>
                    <a href="/network">Network</a>
                </div>
                
                <div class="card">
                    <h3>💰 Wallet Status</h3>
                    <div id="walletStatus">No wallet active</div>
                    <button onclick="location.href='/wallet'">Manage Wallet</button>
                </div>
                
                <div class="card">
                    <h3>⛏️ Mining Status</h3>
                    <div id="miningStatus">Loading...</div>
                    <button onclick="location.href='/mining'">Mining Control</button>
                </div>
                
                <div class="card">
                    <h3>⛓️ Blockchain</h3>
                    <div id="blockchainStatus">Loading...</div>
                </div>

                <div class="card">
                    <h3>🔎 Address Consistency</h3>
                    <div id="addressConsistency">Loading...</div>
                </div>

                <div class="card">
                    <h3>🛡️ Authority Chain</h3>
                    <div id="authorityStatus">Loading...</div>
                </div>

                <div class="card">
                    <h3>🧪 2-Node End-to-End Verification</h3>
                    <div>Runs full flow: start 2 nodes, activate wallets, mine/fund, send, mempool check, confirm block, eviction check.</div>
                    <button onclick="runTwoNodeFlow()">Run Verification</button>
                    <pre id="flowResult">Not started.</pre>
                </div>
            </div>
            
            <script>
                async function updateStatus() {
                    let wallet = null;
                    let mining = null;

                    // Wallet
                    try {
                        const walletResp = await fetch('/api/wallet/info');
                        wallet = await walletResp.json();
                        if (wallet.active) {
                            document.getElementById('walletStatus').innerHTML = `
                                Active: ✅<br>
                                Address: ${wallet.address.substring(0, 32)}...<br>
                                Balance: ${wallet.balance} BERZ
                            `;
                        } else {
                            document.getElementById('walletStatus').innerHTML = 'No wallet active - go to Wallet page';
                        }
                    } catch(e) {}
                    
                    // Mining
                    try {
                        const miningResp = await fetch('/api/mining/info');
                        mining = await miningResp.json();
                        document.getElementById('miningStatus').innerHTML = `
                            Active: ${mining.mining_active ? '✅' : '❌'}<br>
                            Blocks Mined: ${mining.blocks_mined}<br>
                            Hashrate: ${mining.hashrate.toFixed(2)} H/s<br>
                            Target Block Time: ${mining.target_block_time}s
                        `;
                    } catch(e) {}
                    
                    // Blockchain
                    try {
                        const bcResp = await fetch('/api/blockchain');
                        const bc = await bcResp.json();
                        document.getElementById('blockchainStatus').innerHTML = `
                            Height: ${bc.height}<br>
                            Best Hash: ${bc.best_hash.substring(0, 32)}...<br>
                            Difficulty: ${bc.difficulty.toFixed(2)}<br>
                            Target Block Time: ${bc.mining_target_time}s
                        `;
                    } catch(e) {}

                    // Authority chain
                    try {
                        const authResp = await fetch('/api/authority/chain');
                        const auth = await authResp.json();
                        if (!auth.enabled) {
                            document.getElementById('authorityStatus').innerHTML = `
                                Enabled: ❌<br>
                                Verified Nodes: 0<br>
                                Verifiers: 0
                            `;
                        } else {
                            const verified = auth.verified_nodes || [];
                            const verifiers = auth.verifiers || [];
                            const verifiedBy = auth.verified_by || {};
                            document.getElementById('authorityStatus').innerHTML = `
                                Enabled: ✅<br>
                                Verified Nodes: ${verified.length}<br>
                                Verifiers: ${verifiers.length}<br>
                                Last Mapping: ${Object.keys(verifiedBy).length ? JSON.stringify(verifiedBy).substring(0, 120) + '...' : '{}'}
                            `;
                        }
                    } catch(e) {}

                    // Wallet/mining address consistency
                    try {
                        const walletAddr = wallet && wallet.active ? wallet.address : '';
                        const miningAddr = mining && mining.mining_address ? mining.mining_address : '';
                        const mismatch = walletAddr && miningAddr && walletAddr !== miningAddr;
                        document.getElementById('addressConsistency').innerHTML = `
                            Wallet Address: ${walletAddr || '(not active)'}<br>
                            Mining Reward Address: ${miningAddr || '(not set)'}<br>
                            ${mismatch ? '<span class="error">⚠️ Mismatch: mining rewards are not going to active wallet.</span>' : '<span class="success">✅ Addresses aligned.</span>'}
                        `;
                    } catch(e) {}
                }

                async function runTwoNodeFlow() {
                    const out = document.getElementById('flowResult');
                    out.textContent = 'Running verification flow...';
                    try {
                        await fetch('/api/interface/verify-two-node-flow', {method: 'POST'});
                        await updateFlowStatus();
                    } catch (e) {
                        out.textContent = 'Verification failed: ' + e;
                    }
                }

                async function updateFlowStatus() {
                    const out = document.getElementById('flowResult');
                    try {
                        const resp = await fetch('/api/interface/verify-two-node-flow');
                        const data = await resp.json();
                        if (data.running) {
                            out.textContent = `Running verification flow... (${data.elapsed_secs}s)`;
                            return;
                        }
                        if (data.result) {
                            out.textContent = JSON.stringify(data.result, null, 2);
                            return;
                        }
                        if (data.error) {
                            out.textContent = 'Verification failed: ' + data.error;
                            return;
                        }
                        out.textContent = 'Not started.';
                    } catch (e) {
                        out.textContent = 'Verification status error: ' + e;
                    }
                }
                
                updateStatus();
                updateFlowStatus();
                setInterval(updateStatus, 3000);
                setInterval(updateFlowStatus, 1500);
            </script>
        </body>
        </html>
        '''
    
    def _get_wallet_html(self):
        """Get wallet management HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Wallet - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 800px; margin: 0 auto; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 20px; margin: 10px 0; border-radius: 5px; }
                button { background: #00ff00; color: #0a0a0a; padding: 10px; margin: 5px; border: none; cursor: pointer; }
                input, textarea { width: 100%; background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; margin: 5px 0; }
                .warning { color: #ffaa00; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/wallet">Wallet</a>
                    <a href="/mining">Mining</a>
                    <a href="/blocks">Blocks</a>
                    <a href="/mempool">Mempool</a>
                    <a href="/network">Network</a>
                </div>
                
                <h1>💰 Wallet Management</h1>
                
                <div class="card">
                    <h3>🔑 Activate Wallet (with Private Key)</h3>
                    <textarea id="privateKey" rows="2" placeholder="Enter your private key hex"></textarea>
                    <button onclick="activateWallet()">Activate</button>
                    <div id="activationResult"></div>
                </div>
                
                <div class="card">
                    <h3>✨ Create New Wallet</h3>
                    <button onclick="createWallet()">Create New Wallet</button>
                    <div id="newWalletResult"></div>
                </div>

                <div class="card">
                    <h3>🔒 Wallet Lock/Unlock</h3>
                    <input type="password" id="walletPassphrase" placeholder="Wallet passphrase">
                    <input type="number" id="walletUnlockTimeout" placeholder="Unlock timeout seconds" value="300">
                    <button onclick="unlockWallet()">Unlock</button>
                    <button onclick="lockWallet()">Lock</button>
                    <div id="lockResult"></div>
                </div>
                
                <div class="card">
                    <h3>📋 Current Wallet</h3>
                    <div id="walletInfo">No wallet active</div>
                </div>
                
                <div class="card">
                    <h3>💸 Send BerzCoin</h3>
                    <input type="text" id="sendTo" placeholder="Recipient Address">
                    <input type="number" id="sendAmount" placeholder="Amount (BERZ)">
                    <button onclick="sendTransaction()">Send</button>
                    <div id="sendResult"></div>
                </div>
            </div>
            
            <script>
                async function activateWallet() {
                    const privateKey = document.getElementById('privateKey').value;
                    if (!privateKey) {
                        document.getElementById('activationResult').innerHTML = '<span class="warning">Enter private key</span>';
                        return;
                    }
                    
                    const response = await fetch('/api/wallet/activate', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({private_key: privateKey})
                    });
                    const data = await response.json();
                    if (data.address) {
                        document.getElementById('activationResult').innerHTML = `
                            <span class="success">✅ Wallet activated!</span><br>
                            Address: ${data.address}<br>
                            Balance: ${data.balance} BERZ
                        `;
                        loadWalletInfo();
                    } else {
                        document.getElementById('activationResult').innerHTML = `<span class="error">❌ ${data.error}</span>`;
                    }
                }
                
                async function createWallet() {
                    const response = await fetch('/api/wallet/create', {method: 'POST'});
                    const data = await response.json();
                    const extra = data.debug_secrets_allowed ? `
                        <br><span class="warning">Debug secrets enabled (dev-only):</span><br>
                        <strong>Private Key:</strong> ${data.private_key}<br>
                        <strong>Mnemonic:</strong> ${data.mnemonic}
                    ` : '';
                    document.getElementById('newWalletResult').innerHTML = `
                        <span class="success">✅ Wallet created!</span><br>
                        <strong>Public Key:</strong> ${data.public_key.substring(0, 64)}...<br>
                        <strong>Address:</strong> ${data.address}<br>
                        <strong>Balance:</strong> ${data.balance} BERZ
                        ${extra}
                    `;
                }
                
                async function loadWalletInfo() {
                    const response = await fetch('/api/wallet/info');
                    const data = await response.json();
                    if (data.active) {
                        const secretLine = data.debug_secrets_allowed ? `<strong>Mnemonic:</strong> ${data.mnemonic || 'Not available'}<br>` : '';
                        document.getElementById('walletInfo').innerHTML = `
                            <strong>Address:</strong> ${data.address}<br>
                            <strong>Public Key:</strong> ${data.public_key.substring(0, 64)}...<br>
                            <strong>Balance:</strong> ${data.balance} BERZ<br>
                            <strong>Watch-only:</strong> ${data.watch_only ? 'Yes' : 'No'}<br>
                            <strong>Unlocked:</strong> ${data.unlocked ? 'Yes' : 'No'}<br>
                            ${secretLine}
                        `;
                    } else {
                        document.getElementById('walletInfo').innerHTML = 'No wallet active';
                    }
                }

                async function unlockWallet() {
                    const passphrase = document.getElementById('walletPassphrase').value;
                    const timeout = parseInt(document.getElementById('walletUnlockTimeout').value || '300', 10);
                    const response = await fetch('/api/wallet/unlock', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({passphrase, timeout})
                    });
                    const data = await response.json();
                    if (data.status === 'unlocked') {
                        document.getElementById('lockResult').innerHTML = `<span class="success">✅ Unlocked for ${data.timeout}s</span>`;
                        loadWalletInfo();
                    } else {
                        document.getElementById('lockResult').innerHTML = `<span class="error">❌ ${data.error || 'Unlock failed'}</span>`;
                    }
                }

                async function lockWallet() {
                    const response = await fetch('/api/wallet/lock', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({})
                    });
                    const data = await response.json();
                    if (data.status === 'locked') {
                        document.getElementById('lockResult').innerHTML = `<span class="success">✅ Wallet locked</span>`;
                        loadWalletInfo();
                    } else {
                        document.getElementById('lockResult').innerHTML = `<span class="error">❌ ${data.error || 'Lock failed'}</span>`;
                    }
                }
                
                async function sendTransaction() {
                    const to = document.getElementById('sendTo').value;
                    const amount = parseFloat(document.getElementById('sendAmount').value);
                    
                    if (!to || !amount) {
                        document.getElementById('sendResult').innerHTML = '<span class="warning">Enter recipient and amount</span>';
                        return;
                    }
                    
                    const response = await fetch('/api/wallet/send', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({to, amount})
                    });
                    const data = await response.json();
                    if (data.txid) {
                        document.getElementById('sendResult').innerHTML = `
                            <span class="success">✅ Transaction sent!</span><br>
                            TXID: ${data.txid}<br>
                            From: ${data.from}<br>
                            To: ${data.to}<br>
                            Amount: ${data.amount} BERZ
                        `;
                        loadWalletInfo();
                    } else {
                        document.getElementById('sendResult').innerHTML = `<span class="error">❌ ${data.error}</span>`;
                    }
                }
                
                loadWalletInfo();
            </script>
        </body>
        </html>
        '''
    
    def _get_mining_html(self):
        """Get mining control HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mining - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 800px; margin: 0 auto; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 20px; margin: 10px 0; border-radius: 5px; }
                button { background: #00ff00; color: #0a0a0a; padding: 10px 20px; margin: 5px; border: none; cursor: pointer; font-size: 16px; }
                button:hover { background: #00cc00; }
                input { width: 100%; background: #2a2a2a; border: 1px solid #00ff00; color: #00ff00; padding: 8px; margin: 5px 0; }
                .mining-active { color: #00ff00; font-size: 24px; text-align: center; padding: 20px; animation: pulse 1s infinite; }
                .error { color: #ff0000; }
                .success { color: #00ff00; }
                @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/wallet">Wallet</a>
                    <a href="/mining">Mining</a>
                    <a href="/blocks">Blocks</a>
                    <a href="/mempool">Mempool</a>
                    <a href="/network">Network</a>
                </div>
                
                <h1>⛏️ Mining Control</h1>
                
                <div class="card">
                    <h3>📍 Mining Address</h3>
                    <input type="text" id="miningAddress" placeholder="Enter address to receive rewards">
                    <button onclick="setMiningAddress()">Set Address</button>
                    <div id="addressStatus"></div>
                    <div id="addressCompare" style="margin-top: 10px;">Loading...</div>
                </div>
                
                <div class="card">
                    <div id="miningStatus" class="mining-active">⏹️ Mining Stopped</div>
                    <button onclick="startMining()">▶️ Start Mining</button>
                    <button onclick="stopMining()">⏹️ Stop Mining</button>
                </div>
                
                <div class="card">
                    <h3>📊 Mining Statistics</h3>
                    <div id="miningStats">Loading...</div>
                </div>
                
                <div class="card">
                    <h3>⛓️ Network Info</h3>
                    <div id="networkInfo">Loading...</div>
                </div>
            </div>
            
            <script>
                let updateInterval;
                
                async function setMiningAddress() {
                    const address = document.getElementById('miningAddress').value;
                    if (!address) {
                        alert('Enter mining address');
                        return;
                    }
                    
                    const response = await fetch('/api/mining/address', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({address})
                    });
                    const data = await response.json();
                    if (data.address) {
                        document.getElementById('addressStatus').innerHTML = `<span class="success">✅ Address set: ${data.address}</span>`;
                    } else {
                        document.getElementById('addressStatus').innerHTML = `<span class="error">❌ ${data.error || 'Failed to set address'}</span>`;
                    }
                }
                
                async function startMining() {
                    const address = document.getElementById('miningAddress').value;
                    const response = await fetch('/api/mining/start', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({address: address || undefined})
                    });
                    const data = await response.json();
                    if (data.status === 'started') {
                        document.getElementById('miningStatus').innerHTML = '⛏️ MINING ACTIVE ⛏️';
                    } else {
                        alert(data.error);
                    }
                }
                
                async function stopMining() {
                    await fetch('/api/mining/stop', {method: 'POST'});
                    document.getElementById('miningStatus').innerHTML = '⏹️ Mining Stopped';
                }
                
                async function updateStats() {
                    // Mining info
                    const miningResp = await fetch('/api/mining/info');
                    const mining = await miningResp.json();
                    document.getElementById('miningStats').innerHTML = `
                        Blocks Mined: ${mining.blocks_mined}<br>
                        Hashrate: ${mining.hashrate.toFixed(2)} H/s<br>
                        Current Height: ${mining.current_height}<br>
                        Target Block Time: ${mining.target_block_time}s<br>
                        Last Reward Address: ${mining.last_reward_address || '(none yet)'}<br>
                        Last Subsidy: ${mining.last_subsidy_sats} sats<br>
                        Last Fees: ${mining.last_fees_sats} sats<br>
                        Last Total Reward: ${mining.last_reward_sats} sats<br>
                        Last Stop Reason: ${mining.last_stop_reason || '(none)'}
                    `;
                    
                    // Network info
                    const bcResp = await fetch('/api/blockchain');
                    const bc = await bcResp.json();
                    document.getElementById('networkInfo').innerHTML = `
                        Blockchain Height: ${bc.height}<br>
                        Difficulty: ${bc.difficulty.toFixed(2)}<br>
                        Best Hash: ${bc.best_hash.substring(0, 32)}...
                    `;

                    // Address comparison
                    const walletResp = await fetch('/api/wallet/info');
                    const wallet = await walletResp.json();
                    const walletAddr = wallet && wallet.active ? wallet.address : '';
                    const miningAddr = mining && mining.mining_address ? mining.mining_address : '';
                    const mismatch = walletAddr && miningAddr && walletAddr !== miningAddr;
                    document.getElementById('addressCompare').innerHTML = `
                        Wallet Address: ${walletAddr || '(not active)'}<br>
                        Mining Reward Address: ${miningAddr || '(not set)'}<br>
                        ${mismatch ? '<span class="error">⚠️ Mismatch: rewards go to a different address.</span>' : '<span class="success">✅ Addresses aligned.</span>'}
                    `;
                    
                    document.getElementById('miningStatus').innerHTML = mining.mining_active ? '⛏️ MINING ACTIVE ⛏️' : '⏹️ Mining Stopped';
                }
                
                updateStats();
                updateInterval = setInterval(updateStats, 2000);
            </script>
        </body>
        </html>
        '''

    def _get_blocks_html(self):
        """Get blocks explorer HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Blocks - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 16px; margin: 10px 0; border-radius: 5px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { border-bottom: 1px solid #204020; padding: 8px; text-align: left; }
                th { color: #88ff88; }
                .hash { font-size: 12px; color: #9aff9a; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a><a href="/wallet">Wallet</a><a href="/mining">Mining</a><a href="/blocks">Blocks</a><a href="/mempool">Mempool</a><a href="/network">Network</a>
                </div>
                <h1>⛓️ Blocks Explorer</h1>
                <div class="card">
                    <table>
                        <thead>
                            <tr><th>Height</th><th>Hash</th><th>Prev Hash</th><th>Tx</th><th>Time</th><th>Nonce</th><th>Reward (sats)</th></tr>
                        </thead>
                        <tbody id="rows"><tr><td colspan="7">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>
            <script>
                function fmtTs(ts){ try { return new Date(ts * 1000).toLocaleString(); } catch(e){ return ts; } }
                async function loadBlocks() {
                    const res = await fetch('/api/blocks/recent?count=30');
                    const data = await res.json();
                    const rows = (data.blocks || []).map(b => `
                        <tr>
                            <td>${b.height}</td>
                            <td class="hash">${b.hash}</td>
                            <td class="hash">${b.prev_hash.slice(0, 24)}...</td>
                            <td>${b.tx_count}</td>
                            <td>${fmtTs(b.timestamp)}</td>
                            <td>${b.nonce}</td>
                            <td>${b.reward_sats}</td>
                        </tr>
                    `).join('');
                    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="7">No blocks</td></tr>';
                }
                loadBlocks();
                setInterval(loadBlocks, 3000);
            </script>
        </body>
        </html>
        '''

    def _get_mempool_html(self):
        """Get mempool HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mempool - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 16px; margin: 10px 0; border-radius: 5px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { border-bottom: 1px solid #204020; padding: 8px; text-align: left; }
                th { color: #88ff88; }
                .hash { font-size: 12px; color: #9aff9a; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a><a href="/wallet">Wallet</a><a href="/mining">Mining</a><a href="/blocks">Blocks</a><a href="/mempool">Mempool</a><a href="/network">Network</a>
                </div>
                <h1>📦 Mempool</h1>
                <div class="card" id="stats">Loading...</div>
                <div class="card">
                    <table>
                        <thead>
                            <tr><th>TXID</th><th>Inputs</th><th>Outputs</th><th>Fee</th><th>Fee Rate</th><th>Size</th><th>Age(s)</th></tr>
                        </thead>
                        <tbody id="rows"><tr><td colspan="7">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>
            <script>
                async function loadMempool() {
                    const res = await fetch('/api/mempool/entries');
                    const data = await res.json();
                    document.getElementById('stats').innerHTML = `Pending TXs: ${data.count || 0}`;
                    const rows = (data.entries || []).map(e => `
                        <tr>
                            <td class="hash">${e.txid.slice(0, 24)}...</td>
                            <td>${e.inputs}</td>
                            <td>${e.outputs}</td>
                            <td>${e.fee}</td>
                            <td>${e.fee_rate.toFixed(2)}</td>
                            <td>${e.size}</td>
                            <td>${e.age_secs}</td>
                        </tr>
                    `).join('');
                    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="7">Mempool empty</td></tr>';
                }
                loadMempool();
                setInterval(loadMempool, 3000);
            </script>
        </body>
        </html>
        '''

    def _get_network_html(self):
        """Get network/peers HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Network - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 16px; margin: 10px 0; border-radius: 5px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { border-bottom: 1px solid #204020; padding: 8px; text-align: left; }
                th { color: #88ff88; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a><a href="/wallet">Wallet</a><a href="/mining">Mining</a><a href="/blocks">Blocks</a><a href="/mempool">Mempool</a><a href="/network">Network</a>
                </div>
                <h1>🌐 Network / Peers</h1>
                <div class="card" id="stats">Loading...</div>
                <div class="card">
                    <table>
                        <thead>
                            <tr><th>Address</th><th>Direction</th><th>Connected</th><th>Peer Height</th><th>Connected (s)</th></tr>
                        </thead>
                        <tbody id="rows"><tr><td colspan="5">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>
            <script>
                async function loadPeers() {
                    const res = await fetch('/api/network/peers');
                    const data = await res.json();
                    document.getElementById('stats').innerHTML = `Connected peers: ${data.connected || 0}`;
                    const rows = (data.peers || []).map(p => `
                        <tr>
                            <td>${p.address}</td>
                            <td>${p.outbound ? 'outbound' : 'inbound'}</td>
                            <td>${p.connected ? 'yes' : 'no'}</td>
                            <td>${p.peer_height}</td>
                            <td>${p.connected_secs}</td>
                        </tr>
                    `).join('');
                    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="5">No peers</td></tr>';
                }
                loadPeers();
                setInterval(loadPeers, 3000);
            </script>
        </body>
        </html>
        '''
    
