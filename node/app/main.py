"""Main entry point for BerzCoin node."""

import argparse
import asyncio
import signal
import sys
from typing import Any, Optional, Dict

from shared.utils.logging import get_logger, setup_logging
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool
from node.mining.miner import MiningNode
from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from node.p2p.dns_seeds import DNSSeeds
from node.rpc.handlers.blockchain import BlockchainHandlers
from node.rpc.handlers.control import ControlHandlers
from node.rpc.handlers.mempool import MempoolHandlers
from node.rpc.handlers.mining import MiningHandlers
from node.rpc.handlers.mining_control import MiningControlHandlers

from node.rpc.handlers.wallet_control import WalletControlHandlers
from node.rpc.server import RPCServer
from node.storage.blocks_store import BlocksStore
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore
from node.wallet.simple_wallet import SimpleWalletManager
from .components import ComponentManager
from .config import Config
from .modes import ModeManager

logger = get_logger()


class BerzCoinNode:
    """Main BerzCoin node."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = Config(config_path)
        self.mode_manager = ModeManager(self.config)
        self.component_manager = ComponentManager()

        self.network: str = self.config.get("network", "mainnet")
        self.db: Optional[Database] = None
        self.blocks_store: Optional[BlocksStore] = None
        self.utxo_store: Optional[UTXOStore] = None
        self.chainstate: Optional[ChainState] = None
        self.connman: Optional[ConnectionManager] = None
        self.mempool: Optional[Mempool] = None
        self.wallet_manager: Optional[SimpleWalletManager] = None
        self.miner: Optional[MiningNode] = None
        self.rpc_server: Optional[RPCServer] = None
        self.dashboard: Any = None

        self.running = False
        self.sync_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Initialize node subsystems."""
        logger.info("Initializing BerzCoin node...")

        if not self.config.validate():
            return False

        setup_logging(
            level=("DEBUG" if self.config.get("debug") else "INFO"),
            log_file=str(
                self.config.get_datadir() / self.config.get("logfile")
            ),
        )

        if not await self._init_database():
            return False
        if not await self._init_chainstate():
            return False
        if not await self._init_mempool():
            return False
        if not await self._init_p2p():
            return False
        if not await self._init_wallet():
            return False
        if not await self._init_mining():
            return False
        if not await self._init_rpc():
            return False

        logger.info("Node initialized successfully")
        return True

    async def start(self) -> None:
        """Start node services."""
        logger.info("Starting BerzCoin node...")
        self.running = True
        # Ensure expected subsystems are initialized for type checkers
        assert self.chainstate is not None

        if self.connman:
            await self.connman.start()

        if self.rpc_server:
            await self.rpc_server.start()

        # Bootstrap if chain is empty
        if self.chainstate.get_best_height() == -1:
            logger.info("Chain empty, running bootstrap...")
            from node.app.bootstrap import NodeBootstrap

            bootstrap = NodeBootstrap(
                self.chainstate,
                self.connman,
                self.utxo_store,
            )
            try:
                coro = bootstrap.sync_full_chain()
                await asyncio.wait_for(coro, timeout=300)
            except asyncio.TimeoutError:
                logger.warning("Bootstrap timeout")

        if self.connman:
            self.sync_task = asyncio.create_task(self._run_sync_loop())

        # Auto-start mining if configured
        if self.miner and self.config.get("autominer"):
            await self.miner.start_mining(self.config.get("miningaddress"))

        logger.info("Node started")
        await self._wait_for_shutdown()

    async def stop(self) -> None:
        """Stop node services."""
        logger.info("Stopping BerzCoin node...")
        self.running = False

        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass

        if self.miner:
            await self.miner.stop_mining()

        if self.rpc_server:
            await self.rpc_server.stop()

        if self.dashboard:
            await self.dashboard.stop()

        if self.connman:
            await self.connman.stop()

        if self.db:
            self.db.disconnect()

        logger.info("Node stopped")

    async def _run_sync_loop(self) -> None:
        """Run block synchronization loop."""
        assert self.chainstate is not None
        logger.info("Starting block sync loop")

        while self.running:
            try:
                if self.connman and self.connman.get_connected_count() > 0:
                    best_peer = self.connman.get_best_height_peer()

                    if best_peer:
                        cur = self.chainstate.get_best_height()
                        peer_height = best_peer.peer_height

                        if peer_height > cur:
                            logger.info(
                                "Syncing: %d / %d",
                                cur, peer_height,
                            )

                            from node.p2p.sync import BlockSync
                            sync = BlockSync(self.chainstate)
                            await sync.sync_from_peer(best_peer)

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(60)

    async def _init_database(self) -> bool:
        """Initialize database."""
        datadir = self.config.get_datadir()
        network = self.config.get("network")
        self.db = Database(datadir, network)
        self.db.connect()

        migrations = Migrations(self.db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return True

    async def _init_chainstate(self) -> bool:
        """Initialize chainstate."""
        data_dir = str(self.config.get_datadir())
        # ensure database initialized for type checkers
        assert self.db is not None
        self.blocks_store = BlocksStore(self.db, data_dir)
        self.utxo_store = UTXOStore(self.db)
        self.chainstate = ChainState(
            self.db,
            self.config.get_network_params(),
            data_dir,
            blocks_store=self.blocks_store,
            utxo_store=self.utxo_store,
        )
        self.chainstate.initialize()
        # Expose configured network name on chainstate for compatibility
        # with components (miner, tx builder) that expect `chainstate.network`.
        try:
            setattr(self.chainstate, "network", self.config.get("network"))
        except Exception:
            pass
        return True

    async def _init_mempool(self) -> bool:
        """Initialize mempool."""
        if self.mode_manager.is_light_node():
            self.mempool = None
            return True
        # Ensure chainstate available for mempool
        assert self.chainstate is not None
        self.mempool = Mempool(self.chainstate)
        return True

    async def _init_p2p(self) -> bool:
        """Initialize P2P network."""
        is_light = self.mode_manager.is_light_node()
        is_full = self.mode_manager.is_full_node()
        if is_light and not is_full:
            self.connman = None
            return True

        addrman = AddrMan()
        connect_only = self.config.is_connect_only()
        dns_seeds = None
        if not connect_only and self.config.get("dnsseed"):
            dns_seeds = DNSSeeds(self.config.get("dnsseeds"))

        self.connman = ConnectionManager(
            addrman=addrman,
            max_connections=self.config.get("maxconnections"),
            max_outbound=self.config.get("maxoutbound"),
            dns_seeds=dns_seeds,
            node_config=self.config,
            connect_only=connect_only,
        )

        return True

    async def _init_wallet(self) -> bool:
        """Initialize simple wallet manager (private key based)."""
        datadir = self.config.get_datadir()
        self.wallet_manager = SimpleWalletManager(datadir)

        # Check if wallet already exists
        if self.config.get("wallet_private_key"):
            pk = self.config.get("wallet_private_key")
            self.wallet_manager.activate_wallet(pk)
            logger.info("Wallet activated with provided key")

        return True

    async def _init_mining(self) -> bool:
        """Initialize mining node."""
        is_regtest = self.config.get("network") == "regtest"
        use_miner = self.mode_manager.is_mining() or is_regtest
        if not use_miner:
            self.miner = None
            return True

        mining_address = self.config.get("miningaddress")

        # If mining address not set, try to use active wallet
        wm = self.wallet_manager
        if not mining_address and wm and wm.get_active_wallet():
            mining_address = wm.get_active_address()
            if mining_address:
                self.config.set("miningaddress", mining_address)

        # Ensure required subsystems
        assert self.chainstate is not None
        if self.mempool is None:
            # try to initialize a simple mempool if missing
            self.mempool = Mempool(self.chainstate)

        self.miner = MiningNode(
            self.chainstate,
            self.mempool,
            mining_address or "",
            p2p_manager=self.connman,
        )

        return True

    async def _init_rpc(self) -> bool:
        """Initialize RPC server."""
        rpc_host = self.config.get_rpc_bind()
        rpc_port = int(self.config.get("rpcport", 8332))

        self.rpc_server = RPCServer(
            host=rpc_host,
            port=rpc_port,
            rpc_dir=str(self.config.get_datadir()),
            config=self.config,
        )

        # Register handlers
        control = ControlHandlers(self)
        blockchain = BlockchainHandlers(self)
        mempool = MempoolHandlers(self)
        mining = MiningHandlers(self)
        mining_control = MiningControlHandlers(self)
        w_ctrl = WalletControlHandlers(self)  # noqa: F841

        self.rpc_server.register_handlers({
            # Control
            "get_info": control.get_info,
            "stop": control.stop,
            "help": control.help,
            "get_network_info": control.get_network_info,
            "ping": control.ping,
            "uptime": control.uptime,

            # Blockchain
            "get_blockchain_info": blockchain.get_blockchain_info,
            "get_block": blockchain.get_block,
            "get_block_count": blockchain.get_block_count,
            "get_best_block_hash": blockchain.get_best_block_hash,

            # Mempool
            "get_mempool_info": mempool.get_mempool_info,
            "get_raw_mempool": mempool.get_raw_mempool,
            "send_raw_transaction": mempool.send_raw_transaction,

            # Wallet (private key based)
            "create_wallet": self._rpc_create_wallet,
            "activate_wallet": self._rpc_activate_wallet,
            "deactivate_wallet": self._rpc_deactivate_wallet,
            "get_wallet_info": self._rpc_get_wallet_info,
            "get_wallet_balance": self._rpc_get_wallet_balance,
            "get_wallet_address": self._rpc_get_wallet_address,
            "send_to_address": self._rpc_send_to_address,

            # Mining
            "get_mining_info": mining.get_mining_info,
            "get_block_template": mining.get_block_template,
            "submit_block": mining.submit_block,
            "generate": mining.generate,
            "setgenerate": mining_control.set_generate,
            "getminingstatus": mining_control.get_mining_status,
            "setminingaddress": mining_control.set_mining_address,
        })

        # Web dashboard
        if self.config.get("webdashboard", False):
            from node.web.mining_wallet_dashboard import MiningWalletDashboard
            self.dashboard = MiningWalletDashboard(
                self,
                str(self.config.get("webhost", "127.0.0.1")),
                int(self.config.get("webport", 8080)),
            )
            await self.dashboard.start()
            host = self.config.get('webhost')
            port = self.config.get('webport')
            logger.info(
                "Web dashboard on http://%s:%s",
                host, port,
            )

        return True

    # RPC methods for simple wallet

    async def _rpc_create_wallet(self) -> Dict[str, Any]:
        """Create a new wallet (returns private key)."""
        assert self.wallet_manager is not None
        wallet = self.wallet_manager.create_wallet()
        return {
            "private_key": wallet.private_key_hex,
            "public_key": wallet.public_key_hex,
            "address": wallet.address,
            "mnemonic": wallet.mnemonic,
            "warning": "⚠️ SAVE YOUR PRIVATE KEY! You are responsible for it."
        }

    async def _rpc_activate_wallet(self, private_key: str) -> Dict[str, Any]:
        """Activate wallet with private key."""
        assert self.wallet_manager is not None
        wallet = self.wallet_manager.activate_wallet(private_key)
        if not wallet:
            return {"error": "Invalid private key"}

        assert self.chainstate is not None
        bal = self.wallet_manager.get_balance(
            self.chainstate,
        )
        return {
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
            "balance": bal / 100_000_000,
        }

    async def _rpc_deactivate_wallet(self) -> Dict[str, Any]:
        """Deactivate current wallet."""
        assert self.wallet_manager is not None
        self.wallet_manager.deactivate_wallet()
        return {"status": "deactivated"}

    async def _rpc_get_wallet_info(self) -> Dict[str, Any]:
        """Get current wallet info."""
        assert self.wallet_manager is not None
        wallet = self.wallet_manager.get_active_wallet()
        if not wallet:
            return {"active": False}

        assert self.chainstate is not None
        return {
            "active": True,
            "address": wallet.address,
            "public_key": wallet.public_key_hex,
            "private_key": self.wallet_manager.get_active_private_key(),
            "mnemonic": wallet.mnemonic,
            "balance": self.wallet_manager.get_balance(
                self.chainstate,
            ) / 100_000_000,
        }

    async def _rpc_get_wallet_balance(self) -> Dict[str, Any]:
        """Get wallet balance."""
        assert self.wallet_manager is not None
        assert self.chainstate is not None
        balance = self.wallet_manager.get_balance(self.chainstate)
        return {"balance": balance / 100000000, "satoshis": balance}

    async def _rpc_get_wallet_address(self) -> Dict[str, Any]:
        """Get active wallet address."""
        assert self.wallet_manager is not None
        wallet = self.wallet_manager.get_active_wallet()
        if not wallet:
            return {"error": "No active wallet"}

        return {"address": wallet.address}

    async def _rpc_send_to_address(
        self,
        to_address: str,
        amount: float,
        private_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send coins to address."""
        assert self.wallet_manager is not None
        assert self.chainstate is not None
        assert self.mempool is not None
        satoshis = int(amount * 100000000)

        # Use provided private key or active wallet
        if private_key:
            wallet = self.wallet_manager.activate_wallet(private_key)
            if not wallet:
                return {"error": "Invalid private key"}
        elif not self.wallet_manager.get_active_wallet():
            return {"error": "No active wallet. Provide private key."}

        address = self.wallet_manager.get_active_address()
        if not address:
            return {"error": "No active address"}

        # Get UTXOs
        utxos = self.chainstate.get_utxos_for_address(address, 1000)
        if not utxos:
            return {"error": "No UTXOs found"}

        # Select UTXOs
        selected = []
        selected_amount = 0
        for utxo in utxos:
            selected.append(utxo)
            selected_amount += utxo.get('value', 0)
            if selected_amount >= satoshis:
                break

        if selected_amount < satoshis:
            return {"error": "Insufficient funds"}

        # Build transaction
        from node.wallet.core.tx_builder import TransactionBuilder
        builder = TransactionBuilder(self.config.get("network"))

        inputs = [(u['txid'], u['vout'], u['value']) for u in selected]
        outputs = [(to_address, satoshis)]
        change_address = address

        tx = builder.create_transaction(inputs, outputs, change_address)

        # Sign transaction
        from shared.crypto.keys import PrivateKey
        from shared.crypto.signatures import sign_message_hash

        pk_hex = self.wallet_manager.get_active_private_key()
        if not pk_hex:
            return {"error": "No active private key"}
        private_key_obj = PrivateKey(int(pk_hex, 16))

        for i, txin in enumerate(tx.vin):
            sighash = tx.txid()
            signature = sign_message_hash(private_key_obj, sighash)
            txin.script_sig = signature + bytes([0x01])

        # Broadcast
        if await self.mempool.add_transaction(tx):
            return {
                "txid": tx.txid().hex(),
                "from": address,
                "to": to_address,
                "amount": amount
            }

        return {"error": "Transaction rejected"}

    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def request_stop():
            stop_event.set()

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

        await stop_event.wait()
        await self.stop()


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="BerzCoin Node")
    parser.add_argument("-conf", help="Configuration file")
    parser.add_argument("-datadir", help="Data directory")
    parser.add_argument("--testnet", action="store_true", help="Use testnet")
    parser.add_argument("--regtest", action="store_true", help="Use regtest")
    parser.add_argument(
        "--wallet-private-key",
        help="Activate wallet with private key",
    )

    args = parser.parse_args()

    node = BerzCoinNode(args.conf)

    if args.datadir:
        node.config.set("datadir", args.datadir)
    if args.regtest:
        node.config.set("network", "regtest")
    elif args.testnet:
        node.config.set("network", "testnet")
    if args.wallet_private_key:
        node.config.set("wallet_private_key", args.wallet_private_key)

    if not await node.initialize():
        sys.exit(1)

    await node.start()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
