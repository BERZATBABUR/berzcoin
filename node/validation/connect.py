"""Block connection logic."""

from typing import List, Dict, Any
from shared.core.block import Block
from shared.core.transaction import Transaction
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from node.chain.block_index import BlockIndex, BlockIndexEntry
from .disconnect import DisconnectBlock

logger = get_logger()

class ConnectBlock:
    """Connect block to chain."""
    
    def __init__(
        self,
        utxo_store: UTXOStore,
        block_index: BlockIndex,
        network: str = "mainnet",
    ):
        self.utxo_store = utxo_store
        self.block_index = block_index
        self.network = network
        self.disconnect_block = DisconnectBlock(utxo_store, block_index)
    
    def connect(self, block: Block) -> bool:
        height = self.block_index.get_height(block.header.hash_hex())
        if height is None:
            logger.error("Block not in index")
            return False
        logger.debug(f"Connecting block {height}")
        try:
            with self.utxo_store.db.transaction():
                for tx in block.transactions:
                    if not tx.is_coinbase():
                        for txin in tx.vin:
                            success = self.utxo_store.spend_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                            if not success:
                                logger.error("Failed to spend UTXO")
                                return False
                    for i, txout in enumerate(tx.vout):
                        if txout.script_pubkey and txout.script_pubkey[0] == 0x6a:
                            continue
                        address = self._extract_address(txout.script_pubkey)
                        self.utxo_store.add_utxo(
                            txid=tx.txid().hex(),
                            index=i,
                            value=txout.value,
                            script_pubkey=txout.script_pubkey,
                            height=height,
                            is_coinbase=tx.is_coinbase()
                        )
                        if address:
                            self.utxo_store.db.execute("""
                                UPDATE utxo SET address = ?
                                WHERE txid = ? AND "index" = ?
                            """, (address, tx.txid().hex(), i))
                self.block_index.mark_main_chain(block.header.hash_hex(), True)
                self.utxo_store.db.execute("""
                    UPDATE outputs SET spent = 0, spent_by_txid = NULL
                    WHERE txid = ? AND "index" IN (
                        SELECT "index" FROM utxo WHERE txid = ?
                    )
                """, (block.transactions[0].txid().hex(), block.transactions[0].txid().hex()))
            logger.debug(f"Block {height} connected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect block: {e}")
            return False
    
    def _extract_address(self, script_pubkey: bytes) -> str:
        # Address prefixes must match the active network, otherwise the wallet
        # will not recognize mined UTXOs.
        if self.network == "regtest":
            p2pkh_prefix = b"\x6f"
            p2sh_prefix = b"\xc4"
            hrp = "bcrt"
        elif self.network == "testnet":
            p2pkh_prefix = b"\x6f"
            p2sh_prefix = b"\xc4"
            hrp = "tb"
        else:
            p2pkh_prefix = b"\x00"
            p2sh_prefix = b"\x05"
            hrp = "bc"

        if not script_pubkey:
            return ""
        if len(script_pubkey) == 25 and script_pubkey[0] == 0x76 and script_pubkey[1] == 0xa9:
            pubkey_hash = script_pubkey[3:23]
            from shared.crypto.address import base58_check_encode
            return base58_check_encode(p2pkh_prefix + pubkey_hash)
        if len(script_pubkey) == 23 and script_pubkey[0] == 0xa9:
            script_hash = script_pubkey[2:22]
            from shared.crypto.address import base58_check_encode
            return base58_check_encode(p2sh_prefix + script_hash)
        if len(script_pubkey) == 22 and script_pubkey[0] == 0x00 and script_pubkey[1] == 0x14:
            witness_program = script_pubkey[2:22]
            from shared.crypto.bech32 import bech32_encode
            return bech32_encode(hrp, 0, witness_program)
        return ""
    
    def connect_headers(self, headers: List[Any], height: int) -> bool:
        for i, header in enumerate(headers):
            self.block_index.add_block_header(header, height + i)
        return True
