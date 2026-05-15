"""Mining and Wallet Dashboard - Private key based."""

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from aiohttp import web
from aiohttp.web import json_response
from shared.utils.logging import get_logger
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash
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

    def _wallet_manager(self) -> SimpleWalletManager:
        manager = getattr(self.node, "simple_wallet_manager", None)
        if manager is None:
            manager = SimpleWalletManager(
                self.node.config.get_datadir(),
                network=self.node.config.get("network", "mainnet"),
                wallet_passphrase=self.node.config.get("wallet_encryption_passphrase", ""),
                allow_insecure_fallback=bool(self.node.config.get("wallet_allow_insecure_fallback", False)),
                default_unlock_timeout_secs=int(self.node.config.get("wallet_default_unlock_timeout", 300)),
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
        self.app.router.add_get('/api/network/registry', self.network_registry)
        self.app.router.add_post('/api/network/registry/approve', self.network_registry_approve)
        self.app.router.add_post('/api/network/registry/reject', self.network_registry_reject)
        self.app.router.add_get('/api/authority/chain', self.authority_chain_info)
        
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
        if not self.node.miner:
            return json_response({'error': 'Mining subsystem unavailable'}, status=503)
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
        if not self.node.miner:
            return json_response({'error': 'Mining subsystem unavailable'}, status=503)
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
        if not self.node.miner:
            return json_response({'error': 'Mining subsystem unavailable'}, status=503)
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

    def _default_registry_url(self) -> str:
        cfg = getattr(self.node, "config", None)
        if cfg is None:
            return ""
        for key in ("seed_registry", "seedregistry", "seed_registry_url"):
            value = cfg.get(key, "")
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _fetch_registry_payload(self, registry_url: str) -> dict:
        req = urllib.request.Request(registry_url.rstrip("/") + "/peers?all=1", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_registry_action(self, registry_url: str, endpoint: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            registry_url.rstrip("/") + endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def network_registry(self, request):
        """Get seed registry peer state (approved/pending/rejected)."""
        registry_url = str(request.query.get("url", "") or "").strip()
        if not registry_url:
            registry_url = self._default_registry_url()
        if not registry_url:
            return json_response(
                {
                    "registry_url": "",
                    "connected": False,
                    "error": "registry url not configured",
                    "approved": [],
                    "pending": [],
                    "rejected": [],
                }
            )
        try:
            payload = await asyncio.to_thread(self._fetch_registry_payload, registry_url)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            return json_response(
                {
                    "registry_url": registry_url,
                    "connected": False,
                    "error": str(e),
                    "approved": [],
                    "pending": [],
                    "rejected": [],
                },
                status=502,
            )
        peers = payload.get("peers", []) if isinstance(payload, dict) else []
        approved = []
        pending = []
        rejected = []
        for item in peers:
            if not isinstance(item, dict):
                continue
            row = {
                "peer": str(item.get("peer", "")).strip(),
                "status": str(item.get("status", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
                "last_seen": int(item.get("last_seen", 0) or 0),
                "reachable": bool(item.get("reachable", False)),
            }
            if not row["peer"]:
                continue
            status = row["status"].lower()
            if status == "approved":
                approved.append(row)
            elif status == "pending":
                pending.append(row)
            else:
                rejected.append(row)
        return json_response(
            {
                "registry_url": registry_url,
                "connected": True,
                "approved": approved,
                "pending": pending,
                "rejected": rejected,
            }
        )

    async def network_registry_approve(self, request):
        data = await request.json()
        registry_url = str(data.get("url", "") or "").strip() or self._default_registry_url()
        peer = str(data.get("peer", "") or "").strip()
        if not registry_url or not peer:
            return json_response({"ok": False, "error": "url and peer are required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                self._post_registry_action, registry_url, "/approve", {"peer": peer}
            )
            return json_response(payload)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            return json_response({"ok": False, "error": str(e)}, status=502)

    async def network_registry_reject(self, request):
        data = await request.json()
        registry_url = str(data.get("url", "") or "").strip() or self._default_registry_url()
        peer = str(data.get("peer", "") or "").strip()
        reason = str(data.get("reason", "manual_reject") or "manual_reject").strip()
        if not registry_url or not peer:
            return json_response({"ok": False, "error": "url and peer are required"}, status=400)
        try:
            payload = await asyncio.to_thread(
                self._post_registry_action,
                registry_url,
                "/reject",
                {"peer": peer, "reason": reason},
            )
            return json_response(payload)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            return json_response({"ok": False, "error": str(e)}, status=502)

    async def authority_chain_info(self, request):
        """Get authority-chain admission state."""
        connman = getattr(self.node, "connman", None)
        if connman is None:
            return json_response({'enabled': False, 'error': 'P2P not initialized'}, status=503)
        enabled = bool(getattr(connman, "authority_chain_enabled", False))
        if not enabled:
            return json_response(
                {
                    'enabled': False,
                    'verified_nodes': [],
                    'verifiers': [],
                    'verified_by': {},
                    'admission_metrics': {
                        'pending_join_count': 0,
                        'verify_latency_ms_avg': 0.0,
                        'verify_latency_samples': 0,
                        'rejection_reasons': {},
                        'verifier_activity': {},
                    },
                }
            )
        status = connman.authority_chain.get_status()
        status["admission_metrics"] = connman.get_admission_metrics()
        status["enabled"] = True
        return json_response(status)

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
                :root {
                    --bg0: #0b1020;
                    --bg1: #121a33;
                    --card: rgba(15, 24, 48, 0.78);
                    --line: rgba(135, 193, 255, 0.28);
                    --text: #d9eeff;
                    --muted: #98b4d3;
                    --accent: #2de1c2;
                    --accent2: #4aa8ff;
                    --danger: #ff6b7a;
                    --warn: #ffd166;
                }
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body {
                    font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    color: var(--text);
                    padding: 24px;
                    min-height: 100vh;
                    background:
                        radial-gradient(900px 500px at -10% -20%, rgba(74, 168, 255, 0.28), transparent 60%),
                        radial-gradient(1000px 600px at 110% -10%, rgba(45, 225, 194, 0.18), transparent 55%),
                        linear-gradient(160deg, var(--bg0), var(--bg1));
                }
                .container { max-width: 1240px; margin: 0 auto; display: grid; gap: 14px; }
                h1 {
                    color: var(--text);
                    letter-spacing: 0.3px;
                    margin-bottom: 8px;
                    text-shadow: 0 0 18px rgba(74, 168, 255, 0.35);
                }
                .nav {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    background: var(--card);
                    backdrop-filter: blur(6px);
                    border: 1px solid var(--line);
                    border-radius: 12px;
                    padding: 12px;
                    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
                }
                .nav a {
                    color: var(--text);
                    text-decoration: none;
                    padding: 8px 12px;
                    border: 1px solid transparent;
                    border-radius: 9px;
                    transition: all .16s ease;
                }
                .nav a:hover {
                    background: rgba(74, 168, 255, 0.17);
                    border-color: rgba(74, 168, 255, 0.35);
                    transform: translateY(-1px);
                }
                .card {
                    background: var(--card);
                    backdrop-filter: blur(5px);
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    padding: 18px;
                    box-shadow: 0 10px 26px rgba(0, 0, 0, 0.25);
                    transition: transform .16s ease, box-shadow .16s ease;
                }
                .card:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 16px 32px rgba(0, 0, 0, 0.32);
                }
                .card h3 { margin-bottom: 8px; color: #dff7ff; }
                button {
                    background: linear-gradient(135deg, var(--accent), var(--accent2));
                    color: #08212c;
                    padding: 10px 15px;
                    margin: 5px;
                    border: none;
                    border-radius: 10px;
                    cursor: pointer;
                    font-family: inherit;
                    font-weight: 700;
                    transition: transform .14s ease, filter .14s ease;
                }
                button:hover { transform: translateY(-1px); filter: brightness(1.05); }
                input, textarea {
                    background: rgba(7, 16, 33, 0.75);
                    border: 1px solid var(--line);
                    border-radius: 10px;
                    color: var(--text);
                    padding: 9px;
                    margin: 6px 0;
                    font-family: inherit;
                    width: 100%;
                }
                .warning { color: var(--warn); }
                .error { color: var(--danger); }
                .success { color: var(--accent); }
                pre { background: rgba(7, 16, 33, 0.85); padding: 10px; overflow-x: auto; border-radius: 10px; }
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
                                Verifiers: 0<br>
                                Pending Joins: 0<br>
                                Verify Latency Avg: 0 ms
                            `;
                        } else {
                            const verified = auth.verified_nodes || [];
                            const verifiers = auth.verifiers || [];
                            const verifiedBy = auth.verified_by || {};
                            const metrics = auth.admission_metrics || {};
                            const pendingJoinCount = metrics.pending_join_count || 0;
                            const avgLatency = Math.round(metrics.verify_latency_ms_avg || 0);
                            const rejectionReasons = metrics.rejection_reasons || {};
                            const topReason = Object.keys(rejectionReasons).length
                                ? Object.entries(rejectionReasons).sort((a, b) => b[1] - a[1])[0]
                                : null;
                            const verifierActivity = metrics.verifier_activity || {};
                            const activeVerifiers = Object.keys(verifierActivity).length;
                            document.getElementById('authorityStatus').innerHTML = `
                                Enabled: ✅<br>
                                Verified Nodes: ${verified.length}<br>
                                Verifiers: ${verifiers.length}<br>
                                Pending Joins: ${pendingJoinCount}<br>
                                Verify Latency Avg: ${avgLatency} ms (${metrics.verify_latency_samples || 0} samples)<br>
                                Top Rejection: ${topReason ? (topReason[0] + ' (' + topReason[1] + ')') : 'none'}<br>
                                Active Verifiers: ${activeVerifiers}<br>
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

                updateStatus();
                setInterval(updateStatus, 3000);
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
                :root {
                    --bg0: #0b1020; --bg1: #121a33; --card: rgba(15, 24, 48, 0.78);
                    --line: rgba(135, 193, 255, 0.28); --text: #d9eeff;
                    --accent: #2de1c2; --accent2: #4aa8ff; --warn: #ffd166; --danger: #ff6b7a;
                }
                body {
                    background:
                        radial-gradient(900px 500px at -10% -20%, rgba(74, 168, 255, 0.28), transparent 60%),
                        radial-gradient(1000px 600px at 110% -10%, rgba(45, 225, 194, 0.18), transparent 55%),
                        linear-gradient(160deg, var(--bg0), var(--bg1));
                    color: var(--text);
                    font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    padding: 24px;
                }
                .container { max-width: 920px; margin: 0 auto; }
                .card { background: var(--card); border: 1px solid var(--line); padding: 18px; margin: 10px 0; border-radius: 12px; backdrop-filter: blur(5px); }
                button { background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #08212c; padding: 10px 14px; margin: 5px; border: none; border-radius: 10px; cursor: pointer; font-weight: 700; }
                input, textarea { width: 100%; background: rgba(7, 16, 33, 0.75); border: 1px solid var(--line); color: var(--text); padding: 9px; margin: 5px 0; border-radius: 10px; }
                .warning { color: var(--warn); }
                .error { color: var(--danger); }
                .success { color: var(--accent); }
                .nav a { color: var(--text); text-decoration: none; margin: 0 10px; }
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
                :root {
                    --bg0: #0b1020; --bg1: #121a33; --card: rgba(15, 24, 48, 0.78);
                    --line: rgba(135, 193, 255, 0.28); --text: #d9eeff;
                    --accent: #2de1c2; --accent2: #4aa8ff; --danger: #ff6b7a;
                }
                body {
                    background:
                        radial-gradient(900px 500px at -10% -20%, rgba(74, 168, 255, 0.28), transparent 60%),
                        radial-gradient(1000px 600px at 110% -10%, rgba(45, 225, 194, 0.18), transparent 55%),
                        linear-gradient(160deg, var(--bg0), var(--bg1));
                    color: var(--text);
                    font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    padding: 24px;
                }
                .container { max-width: 920px; margin: 0 auto; }
                .card { background: var(--card); border: 1px solid var(--line); padding: 18px; margin: 10px 0; border-radius: 12px; backdrop-filter: blur(5px); }
                button { background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #08212c; padding: 10px 16px; margin: 5px; border: none; border-radius: 10px; cursor: pointer; font-size: 15px; font-weight: 700; }
                input { width: 100%; background: rgba(7, 16, 33, 0.75); border: 1px solid var(--line); color: var(--text); padding: 9px; margin: 5px 0; border-radius: 10px; }
                .mining-active { color: var(--accent); font-size: 24px; text-align: center; padding: 20px; animation: pulse 1.2s infinite; text-shadow: 0 0 14px rgba(45, 225, 194, 0.55); }
                .error { color: var(--danger); }
                .success { color: var(--accent); }
                @keyframes pulse { 0% { opacity: 1; transform: scale(1);} 50% { opacity: 0.72; transform: scale(1.01);} 100% { opacity: 1; transform: scale(1);} }
                .nav a { color: var(--text); text-decoration: none; margin: 0 10px; }
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
                    try {
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
                    } catch (err) {
                        document.getElementById('addressStatus').innerHTML = `<span class="error">❌ Failed to set address (${err})</span>`;
                    }
                }
                
                async function startMining() {
                    const address = document.getElementById('miningAddress').value;
                    try {
                        const response = await fetch('/api/mining/start', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({address: address || undefined})
                        });
                        const data = await response.json();
                        if (data.status === 'started') {
                            document.getElementById('miningStatus').innerHTML = '⛏️ MINING ACTIVE ⛏️';
                        } else {
                            alert(data.error || 'Failed to start mining');
                        }
                    } catch (err) {
                        alert(`Failed to start mining: ${err}`);
                    }
                }
                
                async function stopMining() {
                    try {
                        await fetch('/api/mining/stop', {method: 'POST'});
                        document.getElementById('miningStatus').innerHTML = '⏹️ Mining Stopped';
                    } catch (err) {
                        alert(`Failed to stop mining: ${err}`);
                    }
                }
                
                async function updateStats() {
                    try {
                        // Mining info
                        const miningResp = await fetch('/api/mining/info');
                        const mining = await miningResp.json();
                        document.getElementById('miningStats').innerHTML = `
                            Blocks Mined: ${mining.blocks_mined}<br>
                            Hashrate: ${(Number(mining.hashrate) || 0).toFixed(2)} H/s<br>
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
                        const bestHash = bc.best_hash ? `${bc.best_hash.substring(0, 32)}...` : '(none yet)';
                        document.getElementById('networkInfo').innerHTML = `
                            Blockchain Height: ${bc.height}<br>
                            Difficulty: ${(Number(bc.difficulty) || 0).toFixed(2)}<br>
                            Best Hash: ${bestHash}
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

                        if (miningAddr) {
                            document.getElementById('miningAddress').value = miningAddr;
                        }
                        document.getElementById('miningStatus').innerHTML = mining.mining_active ? '⛏️ MINING ACTIVE ⛏️' : '⏹️ Mining Stopped';
                    } catch (err) {
                        document.getElementById('networkInfo').innerHTML = `<span class="error">Failed to load stats: ${err}</span>`;
                    }
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
                :root {
                    --bg: #020617;
                    --bg-2: #0b1233;
                    --card: rgba(11, 18, 51, 0.78);
                    --text: #dbeafe;
                    --muted: #93c5fd;
                    --accent: #22d3ee;
                    --accent-2: #10b981;
                    --border: rgba(34, 211, 238, 0.35);
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    min-height: 100vh;
                    color: var(--text);
                    font-family: "JetBrains Mono", "Fira Code", "SFMono-Regular", Consolas, monospace;
                    background:
                        radial-gradient(900px 500px at 10% -10%, rgba(34, 211, 238, 0.16), transparent 60%),
                        radial-gradient(900px 500px at 100% 0%, rgba(16, 185, 129, 0.14), transparent 60%),
                        linear-gradient(180deg, var(--bg-2), var(--bg));
                    padding: 26px;
                }
                .container { max-width: 1280px; margin: 0 auto; }
                .nav {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-bottom: 20px;
                }
                .nav a {
                    color: var(--muted);
                    text-decoration: none;
                    padding: 8px 12px;
                    border-radius: 10px;
                    border: 1px solid transparent;
                }
                .nav a:hover { color: var(--text); border-color: var(--border); background: rgba(34, 211, 238, 0.08); }
                h1 { font-size: 44px; margin: 10px 0 18px 0; letter-spacing: 0.4px; }
                .card {
                    background: var(--card);
                    border: 1px solid var(--border);
                    border-radius: 14px;
                    padding: 16px;
                    backdrop-filter: blur(8px);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
                }
                table { width: 100%; border-collapse: collapse; }
                th, td {
                    border-bottom: 1px solid rgba(147, 197, 253, 0.2);
                    padding: 11px 8px;
                    text-align: left;
                }
                th { color: var(--accent); font-weight: 700; }
                .hash { font-size: 12px; color: #bfdbfe; }
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
                :root {
                    --bg: #020617;
                    --bg-2: #0b1233;
                    --card: rgba(11, 18, 51, 0.78);
                    --text: #dbeafe;
                    --muted: #93c5fd;
                    --accent: #22d3ee;
                    --accent-2: #10b981;
                    --border: rgba(34, 211, 238, 0.35);
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    min-height: 100vh;
                    color: var(--text);
                    font-family: "JetBrains Mono", "Fira Code", "SFMono-Regular", Consolas, monospace;
                    background:
                        radial-gradient(900px 500px at 10% -10%, rgba(34, 211, 238, 0.16), transparent 60%),
                        radial-gradient(900px 500px at 100% 0%, rgba(16, 185, 129, 0.14), transparent 60%),
                        linear-gradient(180deg, var(--bg-2), var(--bg));
                    padding: 26px;
                }
                .container { max-width: 1280px; margin: 0 auto; }
                .nav {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-bottom: 20px;
                }
                .nav a {
                    color: var(--muted);
                    text-decoration: none;
                    padding: 8px 12px;
                    border-radius: 10px;
                    border: 1px solid transparent;
                }
                .nav a:hover { color: var(--text); border-color: var(--border); background: rgba(34, 211, 238, 0.08); }
                h1 { font-size: 44px; margin: 10px 0 18px 0; letter-spacing: 0.4px; }
                .card {
                    background: var(--card);
                    border: 1px solid var(--border);
                    border-radius: 14px;
                    padding: 16px;
                    margin-bottom: 12px;
                    backdrop-filter: blur(8px);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
                }
                table { width: 100%; border-collapse: collapse; }
                th, td {
                    border-bottom: 1px solid rgba(147, 197, 253, 0.2);
                    padding: 11px 8px;
                    text-align: left;
                }
                th { color: var(--accent); font-weight: 700; }
                .hash { font-size: 12px; color: #bfdbfe; }
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
                :root {
                    --bg: #020617;
                    --bg-2: #0b1233;
                    --card: rgba(11, 18, 51, 0.78);
                    --text: #dbeafe;
                    --muted: #93c5fd;
                    --accent: #22d3ee;
                    --danger: #fb7185;
                    --border: rgba(34, 211, 238, 0.35);
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    min-height: 100vh;
                    color: var(--text);
                    font-family: "JetBrains Mono", "Fira Code", "SFMono-Regular", Consolas, monospace;
                    background:
                        radial-gradient(900px 500px at 10% -10%, rgba(34, 211, 238, 0.16), transparent 60%),
                        radial-gradient(900px 500px at 100% 0%, rgba(16, 185, 129, 0.14), transparent 60%),
                        linear-gradient(180deg, var(--bg-2), var(--bg));
                    padding: 26px;
                }
                .container { max-width: 1280px; margin: 0 auto; }
                .nav {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-bottom: 20px;
                }
                .nav a {
                    color: var(--muted);
                    text-decoration: none;
                    padding: 8px 12px;
                    border-radius: 10px;
                    border: 1px solid transparent;
                }
                .nav a:hover { color: var(--text); border-color: var(--border); background: rgba(34, 211, 238, 0.08); }
                h1, h2 { margin: 10px 0 18px 0; letter-spacing: 0.4px; }
                h1 { font-size: 44px; }
                h2 { font-size: 26px; }
                .card {
                    background: var(--card);
                    border: 1px solid var(--border);
                    border-radius: 14px;
                    padding: 16px;
                    margin-bottom: 12px;
                    backdrop-filter: blur(8px);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
                }
                table { width: 100%; border-collapse: collapse; }
                th, td {
                    border-bottom: 1px solid rgba(147, 197, 253, 0.2);
                    padding: 11px 8px;
                    text-align: left;
                }
                th { color: var(--accent); font-weight: 700; }
                input[type=text] {
                    width: 65%;
                    background: rgba(2, 6, 23, 0.85);
                    color: var(--text);
                    border: 1px solid var(--border);
                    padding: 10px;
                    border-radius: 10px;
                }
                button {
                    background: linear-gradient(135deg, #22d3ee, #14b8a6);
                    color: #022c22;
                    border: 0;
                    border-radius: 10px;
                    padding: 8px 12px;
                    font-weight: 700;
                    cursor: pointer;
                }
                button:hover { filter: brightness(1.08); transform: translateY(-1px); }
                .btn-reject { background: linear-gradient(135deg, #fb7185, #f43f5e); color: #fff; }
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
                <h2>Seed Registry</h2>
                <div class="card">
                    <label for="registryUrl">Registry URL:</label>
                    <input id="registryUrl" type="text" placeholder="http://127.0.0.1:8787" />
                    <button onclick="loadRegistry()">Load</button>
                    <div id="registryStats" style="margin-top:10px;">Not loaded</div>
                </div>
                <div class="card">
                    <table>
                        <thead>
                            <tr><th>Peer</th><th>Status</th><th>Reason</th><th>Reachable</th><th>Last Seen</th><th>Action</th></tr>
                        </thead>
                        <tbody id="registryRows"><tr><td colspan="6">No data</td></tr></tbody>
                    </table>
                </div>
            </div>
            <script>
                function fmtTs(ts){ if(!ts){ return '-'; } try { return new Date(ts * 1000).toLocaleString(); } catch(e){ return String(ts); } }
                async function loadPeers() {
                    const res = await fetch('/api/network/peers');
                    const data = await res.json();
                    let admissionSummary = '';
                    try {
                        const authRes = await fetch('/api/authority/chain');
                        const auth = await authRes.json();
                        if (auth && auth.enabled) {
                            const m = auth.admission_metrics || {};
                            const pending = m.pending_join_count || 0;
                            const avgLatency = Math.round(m.verify_latency_ms_avg || 0);
                            const samples = m.verify_latency_samples || 0;
                            const rejectCount = Object.values(m.rejection_reasons || {}).reduce((a, b) => a + Number(b || 0), 0);
                            const activeVerifiers = Object.keys(m.verifier_activity || {}).length;
                            admissionSummary =
                                ` | pending_joins=${pending}` +
                                `, verify_latency_avg_ms=${avgLatency}` +
                                `, latency_samples=${samples}` +
                                `, rejections=${rejectCount}` +
                                `, active_verifiers=${activeVerifiers}`;
                        }
                    } catch (e) {}
                    document.getElementById('stats').innerHTML = `Connected peers: ${data.connected || 0}${admissionSummary}`;
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
                async function approvePeer(peer) {
                    const url = (document.getElementById('registryUrl').value || '').trim();
                    await fetch('/api/network/registry/approve', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({url, peer})
                    });
                    await loadRegistry();
                }
                async function rejectPeer(peer) {
                    const url = (document.getElementById('registryUrl').value || '').trim();
                    const reason = prompt('Reject reason', 'manual_reject') || 'manual_reject';
                    await fetch('/api/network/registry/reject', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({url, peer, reason})
                    });
                    await loadRegistry();
                }
                function _registryRowsFromList(items) {
                    return (items || []).map(p => `
                        <tr>
                            <td>${p.peer || ''}</td>
                            <td>${p.status || ''}</td>
                            <td>${p.reason || ''}</td>
                            <td>${p.reachable ? 'yes' : 'no'}</td>
                            <td>${fmtTs(p.last_seen)}</td>
                            <td>
                                <button onclick="approvePeer('${(p.peer || '').replace(/'/g, "\\'")}')" style="margin-right:6px;">Approve</button>
                                <button class="btn-reject" onclick="rejectPeer('${(p.peer || '').replace(/'/g, "\\'")}')">Reject</button>
                            </td>
                        </tr>
                    `).join('');
                }
                async function loadRegistry() {
                    const input = document.getElementById('registryUrl');
                    const url = (input.value || '').trim();
                    const qs = url ? ('?url=' + encodeURIComponent(url)) : '';
                    const res = await fetch('/api/network/registry' + qs);
                    const data = await res.json();
                    if (!url && data.registry_url) {
                        input.value = data.registry_url;
                    }
                    if (!data.connected) {
                        document.getElementById('registryStats').innerHTML = `Registry error: ${data.error || 'unknown'}`;
                        document.getElementById('registryRows').innerHTML = '<tr><td colspan="6">No registry data</td></tr>';
                        return;
                    }
                    const approved = data.approved || [];
                    const pending = data.pending || [];
                    const rejected = data.rejected || [];
                    document.getElementById('registryStats').innerHTML =
                        `Registry: ${data.registry_url} | approved=${approved.length}, pending=${pending.length}, rejected=${rejected.length}`;
                    const rows = _registryRowsFromList(approved.concat(pending).concat(rejected));
                    document.getElementById('registryRows').innerHTML = rows || '<tr><td colspan="6">No peers in registry</td></tr>';
                }
                loadPeers();
                setInterval(loadPeers, 3000);
                loadRegistry();
                setInterval(loadRegistry, 8000);
            </script>
        </body>
        </html>
        '''
    
