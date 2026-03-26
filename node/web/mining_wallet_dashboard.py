"""Mining and Wallet Dashboard - Private key based."""

import json
import time
from aiohttp import web
from aiohttp.web import json_response
from shared.utils.logging import get_logger

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
    
    async def start(self):
        """Start dashboard."""
        self.app = web.Application()
        
        # HTML routes
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/wallet', self.wallet_page)
        self.app.router.add_get('/mining', self.mining_page)
        self.app.router.add_get('/mempool', self.mempool_page)
        
        # API routes
        self.app.router.add_post('/api/wallet/activate', self.activate_wallet)
        self.app.router.add_post('/api/wallet/deactivate', self.deactivate_wallet)
        self.app.router.add_get('/api/wallet/info', self.wallet_info)
        self.app.router.add_post('/api/wallet/create', self.create_wallet)
        self.app.router.add_post('/api/wallet/send', self.send_transaction)
        self.app.router.add_get('/api/wallet/balance', self.get_balance)
        
        self.app.router.add_post('/api/mining/start', self.start_mining)
        self.app.router.add_post('/api/mining/stop', self.stop_mining)
        self.app.router.add_get('/api/mining/info', self.mining_info)
        self.app.router.add_post('/api/mining/address', self.set_mining_address)
        
        self.app.router.add_get('/api/mempool/txs', self.mempool_txs)
        self.app.router.add_get('/api/mempool/stats', self.mempool_stats)
        
        self.app.router.add_get('/api/blockchain', self.blockchain_info)
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        
        logger.info(f"Mining/Wallet Dashboard started on http://{self.host}:{self.port}")
    
    async def index(self, request):
        """Main dashboard page."""
        return web.Response(text=self._get_main_html(), content_type='text/html')
    
    async def wallet_page(self, request):
        """Wallet management page."""
        return web.Response(text=self._get_wallet_html(), content_type='text/html')
    
    async def mining_page(self, request):
        """Mining control page."""
        return web.Response(text=self._get_mining_html(), content_type='text/html')
    
    async def mempool_page(self, request):
        """Mempool viewer page."""
        return web.Response(text=self._get_mempool_html(), content_type='text/html')
    
    # ========== Wallet API ==========
    
    async def create_wallet(self, request):
        """Create new wallet."""
        wallet = self.node.wallet_manager.create_wallet()
        return json_response({
            'private_key': wallet.private_key_hex,
            'public_key': wallet.public_key_hex,
            'address': wallet.address,
            'mnemonic': wallet.mnemonic,
            'warning': '⚠️ SAVE YOUR PRIVATE KEY AND MNEMONIC! You are responsible for them.'
        })
    
    async def activate_wallet(self, request):
        """Activate wallet with private key."""
        data = await request.json()
        private_key = data.get('private_key')
        
        if not private_key:
            return json_response({'error': 'Private key required'}, status=400)
        
        wallet = self.node.wallet_manager.activate_wallet(private_key)
        if wallet:
            return json_response({
                'address': wallet.address,
                'public_key': wallet.public_key_hex,
                'balance': self.node.wallet_manager.get_balance(self.node.chainstate) / 100000000
            })
        
        return json_response({'error': 'Invalid private key'}, status=400)
    
    async def deactivate_wallet(self, request):
        """Deactivate current wallet."""
        self.node.wallet_manager.deactivate_wallet()
        return json_response({'status': 'deactivated'})
    
    async def wallet_info(self, request):
        """Get current wallet info."""
        wallet = self.node.wallet_manager.get_active_wallet()
        if not wallet:
            return json_response({'active': False})
        
        return json_response({
            'active': True,
            'address': wallet.address,
            'public_key': wallet.public_key_hex,
            'private_key': self.node.wallet_manager.get_active_private_key(),
            'mnemonic': wallet.mnemonic,
            'balance': self.node.wallet_manager.get_balance(self.node.chainstate) / 100000000
        })
    
    async def get_balance(self, request):
        """Get wallet balance."""
        balance = self.node.wallet_manager.get_balance(self.node.chainstate)
        return json_response({'balance': balance / 100000000, 'satoshis': balance})
    
    async def send_transaction(self, request):
        """Send transaction."""
        data = await request.json()
        to_address = data.get('to')
        amount = float(data.get('amount', 0))
        private_key = data.get('private_key')
        
        if not to_address or amount <= 0:
            return json_response({'error': 'Invalid parameters'}, status=400)
        
        satoshis = int(amount * 100000000)
        
        # Use provided private key or active wallet
        if private_key:
            # Activate wallet with provided key
            wallet = self.node.wallet_manager.activate_wallet(private_key)
            if not wallet:
                return json_response({'error': 'Invalid private key'}, status=400)
        elif not self.node.wallet_manager.get_active_wallet():
            return json_response({'error': 'No active wallet. Provide private key.'}, status=400)
        
        # Get UTXOs for address
        address = self.node.wallet_manager.get_active_address()
        utxos = self.node.chainstate.get_utxos_for_address(address, 1000)
        
        if not utxos:
            return json_response({'error': 'No UTXOs found for this address'}, status=400)
        
        # Select UTXOs
        selected = []
        selected_amount = 0
        for utxo in utxos:
            selected.append(utxo)
            selected_amount += utxo.get('value', 0)
            if selected_amount >= satoshis:
                break
        
        if selected_amount < satoshis:
            return json_response({'error': 'Insufficient funds'}, status=400)
        
        # Build transaction
        from node.wallet.core.tx_builder import TransactionBuilder
        builder = TransactionBuilder(self.node.config.get('network'))
        
        inputs = [(u['txid'], u['vout'], u['value']) for u in selected]
        outputs = [(to_address, satoshis)]
        change_address = self.node.wallet_manager.get_active_address()
        
        tx = builder.create_transaction(inputs, outputs, change_address)
        
        # Sign transaction
        from shared.crypto.keys import PrivateKey
        from shared.crypto.signatures import sign_message_hash
        
        private_key_obj = PrivateKey(int(self.node.wallet_manager.get_active_private_key(), 16))
        
        for i, txin in enumerate(tx.vin):
            sighash = tx.txid()
            signature = sign_message_hash(private_key_obj, sighash)
            txin.script_sig = signature + bytes([0x01])
        
        # Broadcast
        if await self.node.mempool.add_transaction(tx):
            return json_response({
                'txid': tx.txid().hex(),
                'from': address,
                'to': to_address,
                'amount': amount
            })
        
        return json_response({'error': 'Transaction rejected'}, status=400)
    
    # ========== Mining API ==========
    
    async def start_mining(self, request):
        """Start mining."""
        data = await request.json()
        address = data.get('address')
        
        if address:
            self.node.miner.mining_address = address
            self.node.config.set('miningaddress', address)
        
        if not self.node.miner.mining_address:
            return json_response({'error': 'No mining address set'}, status=400)
        
        await self.node.miner.start_mining()
        return json_response({'status': 'started', 'address': self.node.miner.mining_address})
    
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
            'current_height': self.node.chainstate.get_best_height()
        })
    
    async def set_mining_address(self, request):
        """Set mining address."""
        data = await request.json()
        address = data.get('address')
        
        if not address:
            return json_response({'error': 'Address required'}, status=400)
        
        self.node.miner.mining_address = address
        self.node.config.set('miningaddress', address)
        
        return json_response({'address': address})
    
    # ========== Mempool API ==========
    
    async def mempool_txs(self, request):
        """Get mempool transactions."""
        if not self.node.mempool:
            return json_response({'transactions': []})
        
        txs = await self.node.mempool.get_transactions(100)
        result = []
        
        for tx in txs:
            result.append({
                'txid': tx.txid().hex(),
                'size': len(tx.serialize()),
                'inputs': len(tx.vin),
                'outputs': len(tx.vout)
            })
        
        return json_response({'transactions': result})
    
    async def mempool_stats(self, request):
        """Get mempool statistics."""
        if not self.node.mempool:
            return json_response({'size': 0})
        
        stats = await self.node.mempool.get_stats()
        return json_response({
            'size': stats['size'],
            'bytes': stats['total_size'],
            'fee_total': stats['total_fee']
        })
    
    async def blockchain_info(self, request):
        """Get blockchain info."""
        chain = self.node.chainstate
        return json_response({
            'height': chain.get_best_height(),
            'best_hash': chain.get_best_block_hash(),
            'difficulty': self._get_difficulty(),
            'mining_target_time': 120
        })
    
    def _get_difficulty(self) -> float:
        """Get current difficulty."""
        if not self.node.chainstate:
            return 1.0
        
        best_header = self.node.chainstate.get_best_header()
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
                    <a href="/mempool">Mempool</a>
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
                    <h3>📝 Mempool</h3>
                    <div id="mempoolStatus">Loading...</div>
                    <button onclick="location.href='/mempool'">View Transactions</button>
                </div>
                
                <div class="card">
                    <h3>⛓️ Blockchain</h3>
                    <div id="blockchainStatus">Loading...</div>
                </div>
            </div>
            
            <script>
                async function updateStatus() {
                    // Wallet
                    try {
                        const walletResp = await fetch('/api/wallet/info');
                        const wallet = await walletResp.json();
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
                        const mining = await miningResp.json();
                        document.getElementById('miningStatus').innerHTML = `
                            Active: ${mining.mining_active ? '✅' : '❌'}<br>
                            Blocks Mined: ${mining.blocks_mined}<br>
                            Hashrate: ${mining.hashrate.toFixed(2)} H/s<br>
                            Target Block Time: ${mining.target_block_time}s
                        `;
                    } catch(e) {}
                    
                    // Mempool
                    try {
                        const mempoolResp = await fetch('/api/mempool/stats');
                        const mempool = await mempoolResp.json();
                        document.getElementById('mempoolStatus').innerHTML = `
                            Pending Transactions: ${mempool.size}<br>
                            Total Size: ${(mempool.bytes / 1024).toFixed(2)} KB
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
                    <a href="/mempool">Mempool</a>
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
                    <button onclick="deactivateWallet()">Deactivate</button>
                </div>
                
                <div class="card">
                    <h3>💸 Send BerzCoin</h3>
                    <input type="text" id="sendTo" placeholder="Recipient Address">
                    <input type="number" id="sendAmount" placeholder="Amount (BERZ)">
                    <input type="text" id="sendPrivateKey" placeholder="Your Private Key (or use active wallet)">
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
                    document.getElementById('newWalletResult').innerHTML = `
                        <span class="success">✅ Wallet created!</span><br>
                        <span class="warning">⚠️ SAVE THESE CREDENTIALS! You are responsible!</span><br>
                        <strong>Private Key:</strong> ${data.private_key}<br>
                        <strong>Public Key:</strong> ${data.public_key.substring(0, 64)}...<br>
                        <strong>Address:</strong> ${data.address}<br>
                        <strong>Mnemonic:</strong> ${data.mnemonic}
                    `;
                }
                
                async function loadWalletInfo() {
                    const response = await fetch('/api/wallet/info');
                    const data = await response.json();
                    if (data.active) {
                        document.getElementById('walletInfo').innerHTML = `
                            <strong>Address:</strong> ${data.address}<br>
                            <strong>Public Key:</strong> ${data.public_key.substring(0, 64)}...<br>
                            <strong>Balance:</strong> ${data.balance} BERZ<br>
                            <strong>Mnemonic:</strong> ${data.mnemonic || 'Not available'}
                        `;
                    } else {
                        document.getElementById('walletInfo').innerHTML = 'No wallet active';
                    }
                }
                
                async function deactivateWallet() {
                    await fetch('/api/wallet/deactivate', {method: 'POST'});
                    loadWalletInfo();
                }
                
                async function sendTransaction() {
                    const to = document.getElementById('sendTo').value;
                    const amount = parseFloat(document.getElementById('sendAmount').value);
                    const privateKey = document.getElementById('sendPrivateKey').value;
                    
                    if (!to || !amount) {
                        document.getElementById('sendResult').innerHTML = '<span class="warning">Enter recipient and amount</span>';
                        return;
                    }
                    
                    const response = await fetch('/api/wallet/send', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({to, amount, private_key: privateKey || undefined})
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
                    <a href="/mempool">Mempool</a>
                </div>
                
                <h1>⛏️ Mining Control</h1>
                
                <div class="card">
                    <h3>📍 Mining Address</h3>
                    <input type="text" id="miningAddress" placeholder="Enter address to receive rewards">
                    <button onclick="setMiningAddress()">Set Address</button>
                    <div id="addressStatus"></div>
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
                    document.getElementById('addressStatus').innerHTML = `<span class="success">✅ Address set: ${data.address}</span>`;
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
                        Target Block Time: ${mining.target_block_time}s
                    `;
                    
                    // Network info
                    const bcResp = await fetch('/api/blockchain');
                    const bc = await bcResp.json();
                    document.getElementById('networkInfo').innerHTML = `
                        Blockchain Height: ${bc.height}<br>
                        Difficulty: ${bc.difficulty.toFixed(2)}<br>
                        Best Hash: ${bc.best_hash.substring(0, 32)}...
                    `;
                    
                    document.getElementById('miningStatus').innerHTML = mining.mining_active ? '⛏️ MINING ACTIVE ⛏️' : '⏹️ Mining Stopped';
                }
                
                updateStats();
                updateInterval = setInterval(updateStats, 2000);
            </script>
        </body>
        </html>
        '''
    
    def _get_mempool_html(self):
        """Get mempool viewer HTML."""
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Mempool - BerzCoin</title>
            <style>
                body { background: #0a0a0a; color: #00ff00; font-family: monospace; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .card { background: #1a1a1a; border: 1px solid #00ff00; padding: 20px; margin: 10px 0; border-radius: 5px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { text-align: left; padding: 8px; border-bottom: 1px solid #2a2a2a; }
                .nav a { color: #00ff00; text-decoration: none; margin: 0 10px; }
                .txid { font-family: monospace; font-size: 12px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/wallet">Wallet</a>
                    <a href="/mining">Mining</a>
                    <a href="/mempool">Mempool</a>
                </div>
                
                <h1>📝 Mempool Viewer</h1>
                
                <div class="card">
                    <h3>Pending Transactions</h3>
                    <div id="mempoolStats">Loading...</div>
                    <div id="mempoolTxs">Loading...</div>
                </div>
            </div>
            
            <script>
                async function updateMempool() {
                    const statsResp = await fetch('/api/mempool/stats');
                    const stats = await statsResp.json();
                    document.getElementById('mempoolStats').innerHTML = `
                        Transactions: ${stats.size}<br>
                        Size: ${(stats.bytes / 1024).toFixed(2)} KB<br>
                        Total Fees: ${(stats.fee_total / 100000000).toFixed(8)} BERZ
                    `;
                    
                    const txsResp = await fetch('/api/mempool/txs');
                    const txs = await txsResp.json();
                    if (txs.transactions && txs.transactions.length > 0) {
                        let html = '<table><tr><th>TXID</th><th>Size</th><th>Inputs</th><th>Outputs</th></tr>';
                        for (const tx of txs.transactions) {
                            html += `<tr>
                                <td class="txid">${tx.txid.substring(0, 32)}...</td>
                                <td>${tx.size}</td>
                                <td>${tx.inputs}</td>
                                <td>${tx.outputs}</td>
                            </tr>`;
                        }
                        html += '</table>';
                        document.getElementById('mempoolTxs').innerHTML = html;
                    } else {
                        document.getElementById('mempoolTxs').innerHTML = 'No pending transactions';
                    }
                }
                
                updateMempool();
                setInterval(updateMempool, 3000);
            </script>
        </body>
        </html>
        '''
