"""Main entry point for BerzCoin node."""

import argparse
import asyncio
import signal
import sys
from typing import Any, Optional

from shared.utils.logging import get_logger, setup_logging
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool
from node.mining.block_assembler import BlockAssembler
from node.mining.miner import CPUMiner
from node.p2p.addrman import AddrMan
from node.p2p.connman import ConnectionManager
from node.p2p.dns_seeds import DNSSeeds
from node.rpc.handlers.blockchain import BlockchainHandlers
from node.rpc.handlers.control import ControlHandlers
from node.rpc.handlers.mempool import MempoolHandlers
from node.rpc.handlers.mining import MiningHandlers
from node.rpc.handlers.mining_control import MiningControlHandlers
from node.rpc.handlers.wallet import WalletHandlers
from node.rpc.handlers.wallet_control import WalletControlHandlers
from node.rpc.server import RPCServer
from node.storage.blocks_store import BlocksStore
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore
from node.wallet.core.wallet import Wallet
from .components import ComponentManager
from .config import Config
from .modes import ModeManager

logger = get_logger()


class BerzCoinNode:
    """Main BerzCoin node."""

    def __init__(self, config_path: str = None):
        self.config = Config(config_path)
        self.mode_manager = ModeManager(self.config)
        self.component_manager = ComponentManager()

        self.network: str = self.config.get("network", "mainnet")
        self.db: Database = None  # type: ignore[assignment]
        self.blocks_store: Optional[BlocksStore] = None  # type: ignore[assignment]
        self.utxo_store: Optional[UTXOStore] = None  # type: ignore[assignment]
        self.chainstate: ChainState = None  # type: ignore[assignment]
        self.connman: ConnectionManager = None  # type: ignore[assignment]
        self.mempool: Mempool = None  # type: ignore[assignment]
        self.wallet: Wallet = None  # type: ignore[assignment]
        self.miner: CPUMiner = None  # type: ignore[assignment]
        self.rpc_server: RPCServer = None  # type: ignore[assignment]
        self.dashboard: Any = None  # type: ignore[assignment]  # node.web.dashboard.WebDashboard

        self.running = False
        self.sync_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Initialize node subsystems (database, chain, optional reindex, mempool, …)."""
        logger.info("Initializing BerzCoin node...")
        self.mode_manager = ModeManager(self.config)
        self.network = self.config.get("network", "mainnet")

        if not self.config.validate():
            return False

        reindex = bool(self.config.get("reindex", False))

        setup_logging(
            level="DEBUG" if self.config.get("debug") else "INFO",
            log_file=str(self.config.get_datadir() / self.config.get("logfile")),
            debug=bool(self.config.get("debug")),
        )

        if not await self._init_database():
            return False
        if not await self._init_chainstate():
            return False

        if reindex:
            logger.info("Reindexing blockchain...")
            from .reindex import Reindexer

            reindexer = Reindexer(
                self.chainstate,
                self.chainstate.blocks_store,
                self.chainstate.utxo_store,
            )
            if not await reindexer.run():
                logger.error("Reindex failed")
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
        """Start node services and block sync loop."""
        logger.info("Starting BerzCoin node...")
        self.running = True

        if self.connman:
            await self.connman.start()

        if self.rpc_server:
            await self.rpc_server.start()

        # Important: start RPC before blocking bootstrap so operators/CLIs can call into the node.
        if self.connman and self.chainstate.get_best_height() == -1:
            logger.info(
                "Chain has no blocks; running initial bootstrap (timeout 300s)"
            )
            from node.app.bootstrap import Bootstrap

            bootstrap = Bootstrap(self.chainstate, self.connman)
            try:
                await asyncio.wait_for(bootstrap.run(), timeout=300)
            except asyncio.TimeoutError:
                logger.warning("Bootstrap timed out; continuing with background sync")

        if self.connman:
            self.sync_task = asyncio.create_task(self._run_sync_loop())

        if self.miner:
            autostart = bool(self.config.get("mining")) or (
                self.config.get("network") == "regtest"
                and bool(self.config.get("autominer", False))
            )
            if autostart:
                await self.miner.start_mining(self.config.get("miningaddress"))

        logger.info("Node started")
        await self._wait_for_shutdown()

    async def stop(self) -> None:
        """Stop node services."""
        if not self.running and not self.rpc_server:
            return
        logger.info("Stopping BerzCoin node...")
        self.running = False

        if self.sync_task:
            self.sync_task.cancel()
            try:
                await self.sync_task
            except asyncio.CancelledError:
                pass
            self.sync_task = None

        if self.miner:
            await self.miner.stop_mining()
        if self.rpc_server:
            await self.rpc_server.stop()
        if self.dashboard:
            await self.dashboard.stop()
            self.dashboard = None
        if self.connman:
            await self.connman.stop()
        if self.db:
            self.db.disconnect()

        logger.info("Node stopped")

    async def _run_sync_loop(self) -> None:
        """Run block synchronization loop."""
        logger.info("Starting block sync loop")

        while self.running:
            try:
                if self.connman and self.connman.get_connected_count() > 0:
                    best_peer = self.connman.get_best_height_peer()

                    if best_peer:
                        current_height = self.chainstate.get_best_height()
                        peer_height = best_peer.peer_height

                        if peer_height > current_height:
                            logger.info(f"Syncing: {current_height} / {peer_height}")

                            from node.p2p.sync import BlockSync

                            sync = BlockSync(self.chainstate)
                            await sync.sync_from_peer(best_peer)

                            await self._download_missing_blocks(
                                best_peer, current_height, peer_height
                            )

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(60)

    async def _download_missing_blocks(self, peer, start_height: int, end_height: int) -> None:
        """Download missing blocks from peer for heights (start_height, end_height]."""
        from shared.protocol.messages import InvMessage

        for height in range(start_height + 1, end_height + 1):
            hdr = self.chainstate.header_chain.get_header(height)
            if hdr:
                await peer.send_getdata(InvMessage.InvType.MSG_BLOCK, hdr.hash())
                logger.debug(f"Requested block {height}")

            await asyncio.sleep(0.1)

    async def _init_database(self) -> bool:
        datadir = self.config.get_datadir()
        network = self.config.get("network")
        self.db = Database(datadir, network)
        self.db.connect()

        migrations = Migrations(self.db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return True

    async def _init_chainstate(self) -> bool:
        """Initialize chainstate and shared block/UTXO stores."""
        data_dir = str(self.config.get_datadir())
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
        return True

    async def _init_mempool(self) -> bool:
        """Initialize mempool."""
        if self.mode_manager.is_light_node():
            self.mempool = None  # type: ignore[assignment]
            return True

        self.mempool = Mempool(self.chainstate)
        # Connection manager for inv broadcast; updated in _init_p2p when connman exists
        self.mempool.connman = self.connman

        return True

    async def _init_p2p(self) -> bool:
        """Initialize P2P network."""
        if self.mode_manager.is_light_node() and not self.mode_manager.is_full_node():
            self.connman = None  # type: ignore[assignment]
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

        if hasattr(self, "mempool") and self.mempool:
            self.mempool.connman = self.connman

        return True

    async def _init_wallet(self) -> bool:
        if not self.mode_manager.has_wallet():
            self.wallet = None  # type: ignore[assignment]
            return True

        wallet_path = self.config.get_datadir() / "wallets" / self.config.get("wallet")
        wallet_path.parent.mkdir(parents=True, exist_ok=True)
        self.wallet = Wallet(str(wallet_path), self.config.get("network"))

        passphrase = (self.config.get("walletpassphrase") or "").strip()
        if wallet_path.exists():
            if not passphrase:
                logger.error("walletpassphrase is required to load an existing wallet")
                return False
            try:
                if not self.wallet.load(passphrase):
                    logger.warning("Failed to load wallet")
                    return False
            except ValueError as e:
                logger.error("%s", e)
                return False
        else:
            if not passphrase:
                logger.error("walletpassphrase is required to create a new encrypted wallet")
                return False
            try:
                mnemonic = self.wallet.create(passphrase)
                logger.info("Created new wallet (mnemonic length %s chars)", len(mnemonic))
            except ValueError as e:
                logger.error("%s", e)
                return False

        return True

    async def _init_mining(self) -> bool:
        """Initialize CPU miner when ``mining`` mode is on, or on regtest (for RPC/dashboard without forcing mine on boot)."""
        use_miner = self.mode_manager.is_mining() or self.config.get("network") == "regtest"
        if not use_miner:
            self.miner = None  # type: ignore[assignment]
            return True

        block_assembler = BlockAssembler(
            self.chainstate,
            self.mempool,
            self.config.get("miningaddress"),
            network=self.config.get("network"),
        )
        self.miner = CPUMiner(
            self.chainstate,
            block_assembler,
            self.config.get("miningaddress"),
        )

        if self.config.get("network") == "regtest" and self.config.get("autominer", False):
            asyncio.create_task(
                self.miner.start_mining(self.config.get("miningaddress"), threads=1)
            )
            logger.info("Auto-mining enabled on regtest")

        return True

    async def _init_rpc(self) -> bool:
        """Initialize JSON-RPC server with secure binding (rpcbind + rpcallowip via get_rpc_bind)."""
        rpc_host = self.config.get_rpc_bind()
        rpc_port = int(self.config.get("rpcport", 8332))

        self.rpc_server = RPCServer(
            host=rpc_host,
            port=rpc_port,
            rpc_dir=str(self.config.get_datadir()),
            config=self.config,
        )

        control = ControlHandlers(self)
        blockchain = BlockchainHandlers(self)
        mempool = MempoolHandlers(self)
        wallet = WalletHandlers(self)
        mining = MiningHandlers(self)
        mining_control = MiningControlHandlers(self)
        wallet_control = WalletControlHandlers(self)

        self.rpc_server.register_handlers(
            {
                "get_info": control.get_info,
                "stop": control.stop,
                "help": control.help,
                "get_blockchain_info": blockchain.get_blockchain_info,
                "get_block": blockchain.get_block,
                "get_block_count": blockchain.get_block_count,
                "get_best_block_hash": blockchain.get_best_block_hash,
                "get_mempool_info": mempool.get_mempool_info,
                "get_raw_mempool": mempool.get_raw_mempool,
                "get_mempool_entry": mempool.get_mempool_entry,
                "send_raw_transaction": mempool.send_raw_transaction,
                "test_mempool_accept": mempool.test_mempool_accept,
                "get_wallet_info": wallet.get_wallet_info,
                "get_balance": wallet.get_balance,
                "get_new_address": wallet.get_new_address,
                "send_to_address": wallet.send_to_address,
                "get_mining_info": mining.get_mining_info,
                "get_block_template": mining.get_block_template,
                "submit_block": mining.submit_block,
                "generate": mining.generate,
                "setgenerate": mining_control.set_generate,
                "getminingstatus": mining_control.get_mining_status,
                "setminingaddress": mining_control.set_mining_address,
                "getminingtemplates": mining_control.get_mining_templates,
                "getminingworkers": mining_control.get_mining_workers,
                "setminingdifficulty": mining_control.set_mining_difficulty,
                "listwallets": wallet_control.list_wallets,
                "loadwallet": wallet_control.load_wallet,
                "createwallet": wallet_control.create_wallet,
                "unloadwallet": wallet_control.unload_wallet,
                "backupwallet": wallet_control.backup_wallet,
                "restorewallet": wallet_control.restore_wallet,
                "listbackups": wallet_control.list_backups,
                "getwalletaddresses": wallet_control.get_wallet_addresses,
                "getwalletutxos": wallet_control.get_wallet_utxos,
                "getwallettransactions": wallet_control.get_wallet_transactions,
                "setwalletlabel": wallet_control.set_wallet_label,
                "lockwallet": wallet_control.lock_wallet,
                "unlockwallet": wallet_control.unlock_wallet,
                "getwalletaccounts": wallet_control.get_wallet_accounts,
                "createaccount": wallet_control.create_account,
                "getwalletsummary": wallet_control.get_wallet_summary,
            }
        )

        if self.config.get("webdashboard", False):
            # Prefer the enhanced dashboard UI.
            from node.web.enhanced_dashboard import EnhancedDashboard

            self.dashboard = EnhancedDashboard(
                self,
                str(self.config.get("webhost", "127.0.0.1")),
                int(self.config.get("webport", 8080)),
                require_auth=bool(self.config.get("web_require_auth", False)),
            )
            await self.dashboard.start()
            logger.info(
                "Web dashboard on http://%s:%s",
                self.config.get("webhost", "127.0.0.1"),
                self.config.get("webport", 8080),
            )

        return True

    async def _wait_for_shutdown(self) -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def request_stop() -> None:
            stop_event.set()

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            logger.warning("Signal handlers not available; use Ctrl+C may not stop cleanly")

        await stop_event.wait()
        await self.stop()


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="BerzCoin Node")
    parser.add_argument("-conf", help="Configuration file")
    parser.add_argument("-datadir", help="Data directory")
    parser.add_argument("-reindex", action="store_true", help="Reindex blockchain")
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use testnet parameters (overrides config network)",
    )
    parser.add_argument(
        "--regtest",
        action="store_true",
        help="Use regtest parameters (overrides config network)",
    )
    parser.add_argument(
        "--disablewallet",
        action="store_true",
        help="Disable wallet / skip wallet initialization (chain-only node)",
    )
    parser.add_argument(
        "--walletpassphrase",
        default=None,
        help="Wallet encryption passphrase (overrides config; avoid on shared systems — visible in process list)",
    )
    args = parser.parse_args()

    node = BerzCoinNode(args.conf)
    if args.datadir:
        node.config.set("datadir", args.datadir)
    if args.regtest:
        node.config.set("network", "regtest")
    elif args.testnet:
        node.config.set("network", "testnet")

    if args.reindex:
        node.config.set("reindex", True)
    if args.disablewallet:
        node.config.set("disablewallet", True)
    if args.walletpassphrase is not None:
        node.config.set("walletpassphrase", args.walletpassphrase)

    if not await node.initialize():
        sys.exit(1)

    await node.start()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
