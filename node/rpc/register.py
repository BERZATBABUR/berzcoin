"""Wire JSON-RPC method names to handler callables."""

from typing import Any, Dict, Callable, Awaitable

from .server import RPCServer
from .handlers import (
    ControlHandlers,
    BlockchainHandlers,
    MempoolHandlers,
    WalletHandlers,
    MiningHandlers,
)

Handler = Callable[..., Awaitable[Any]]


def register_default_handlers(server: RPCServer, node: Any) -> None:
    """Register all default RPC methods on ``server`` using ``node``."""
    control = ControlHandlers(node)
    chain = BlockchainHandlers(node)
    mempool = MempoolHandlers(node)
    wallet = WalletHandlers(node)
    mining = MiningHandlers(node)

    methods: Dict[str, Handler] = {
        'get_info': control.get_info,
        'stop': control.stop,
        'help': control.help,
        'get_memory_info': control.get_memory_info,
        'get_network_info': control.get_network_info,
        'ping': control.ping,
        'uptime': control.uptime,
        'get_blockchain_info': chain.get_blockchain_info,
        'get_block': chain.get_block,
        'get_block_header': chain.get_block_header,
        'get_best_block_hash': chain.get_best_block_hash,
        'get_block_count': chain.get_block_count,
        'get_block_hash': chain.get_block_hash,
        'get_block_stats': chain.get_block_stats,
        'get_chaintips': chain.get_chaintips,
        'get_tx_out': chain.get_tx_out,
        'get_mempool_info': mempool.get_mempool_info,
        'get_raw_mempool': mempool.get_raw_mempool,
        'get_mempool_entry': mempool.get_mempool_entry,
        'send_raw_transaction': mempool.send_raw_transaction,
        'test_mempool_accept': mempool.test_mempool_accept,
        'get_wallet_info': wallet.get_wallet_info,
        'get_balance': wallet.get_balance,
        'get_new_address': wallet.get_new_address,
        'send_to_address': wallet.send_to_address,
        'list_unspent': wallet.list_unspent,
        'list_transactions': wallet.list_transactions,
        'create_wallet': wallet.create_wallet,
        'load_wallet': wallet.load_wallet,
        'get_address_info': wallet.get_address_info,
        'get_mining_info': mining.get_mining_info,
        'get_block_template': mining.get_block_template,
        'submit_block': mining.submit_block,
        'get_network_hashps': mining.get_network_hashps,
        'get_difficulty': mining.get_difficulty,
        'generate': mining.generate,
    }

    server.register_handlers(methods)
