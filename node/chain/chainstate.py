"""Active chain state management."""

from typing import Optional, List, Dict, Any
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction
from shared.consensus.params import ConsensusParams
from shared.consensus.rules import ConsensusRules
from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger
from node.storage.db import Database
from node.storage.blocks_store import BlocksStore
from node.storage.utxo_store import UTXOStore
from .block_index import BlockIndex, BlockIndexEntry
from .headers import HeaderChain
from .chainwork import ChainWork

logger = get_logger()

class ChainState:
    """Active chain state."""
    
    def __init__(
        self,
        db: Database,
        params: ConsensusParams,
        data_dir: str,
        blocks_store: Optional[BlocksStore] = None,
        utxo_store: Optional[UTXOStore] = None,
    ):
        self.db = db
        self.params = params
        self.blocks_store = blocks_store if blocks_store is not None else BlocksStore(db, data_dir)
        self.utxo_store = utxo_store if utxo_store is not None else UTXOStore(db)
        self.header_chain = HeaderChain(db, self.blocks_store)
        self.block_index = BlockIndex(db)
        self.chainwork = ChainWork(params)
        self.rules = ConsensusRules(params, output_value_lookup=self._lookup_output_value)
        self.pow = ProofOfWork(params)
        self._block_validator = None
        
        self._best_hash: Optional[str] = None
        self._best_height: int = -1
        self._best_chainwork: int = 0
        self._wallet_callback = None

    def _lookup_output_value(self, txid: str, index: int) -> Optional[int]:
        result = self.db.fetch_one(
            'SELECT value FROM outputs WHERE txid = ? AND "index" = ?',
            (txid, int(index)),
        )
        if not result or result.get("value") is None:
            return None
        return int(result["value"])
    
    def initialize(self) -> None:
        self.block_index.load()
        self._best_height = self.block_index.get_best_height()
        self._best_hash = self.block_index.get_best_hash()
        if self._best_height < 0 and self.params.get_network_name() == "regtest":
            logger.info(
                "Empty regtest datadir detected; importing a synthetic genesis block"
            )
            self._import_synthetic_genesis_for_regtest()
            self.block_index.clear()
            self.block_index.load()
            self._best_height = self.block_index.get_best_height()
            self._best_hash = self.block_index.get_best_hash()
        if self._best_hash:
            entry = self.block_index.get_block(self._best_hash)
            if entry:
                self._best_chainwork = entry.chainwork
        logger.info(f"Chain state initialized: height={self._best_height}, work={self._best_chainwork}")

    def _import_synthetic_genesis_for_regtest(self) -> None:
        """Insert a minimal genesis block so mining works on a fresh regtest datadir.

        This repo does not ship a regtest genesis JSON. For local/regtest demos
        we create a valid coinbase block at height 0:
        - header PoW is mined at an easy target derived from regtest pow_limit
        - merkle root matches the single coinbase txid
        - consensus rules here do not hard-check the canonical mainnet genesis
        """
        # Local imports to keep module load light.
        from shared.core.merkle import merkle_root
        from shared.core.transaction import TxIn, TxOut

        # Create a minimal coinbase transaction (value 0 is allowed by subsidy rule).
        coinbase_script = b"\x01\x00"  # must be 2..100 bytes for coinbase validation
        coinbase_tx = Transaction(
            version=1,
            inputs=[
                TxIn(
                    prev_tx_hash=b"\x00" * 32,
                    prev_tx_index=0xFFFFFFFF,
                    script_sig=coinbase_script,
                    sequence=0xFFFFFFFF,
                )
            ],
            outputs=[
                TxOut(
                    value=0,
                    script_pubkey=b"",
                )
            ],
            locktime=0,
        )

        txid = coinbase_tx.txid()
        mr = merkle_root([txid]) or (b"\x00" * 32)

        # Keep regtest genesis deterministic across machines/runs so nodes can peer.
        # Using wall-clock time here creates different height-0 blocks per node.
        genesis_bits = int(self.params.genesis_bits)
        genesis_timestamp = int(self.params.genesis_time)

        header = BlockHeader(
            version=self.params.genesis_version,
            prev_block_hash=b"\x00" * 32,
            merkle_root=mr,
            timestamp=genesis_timestamp,
            bits=genesis_bits,
            nonce=0,
        )

        # Mine nonce so PoW validates under header.bits.
        if not self.pow.mine(header, max_nonce=5_000_000):
            raise RuntimeError("Failed to mine synthetic regtest genesis")

        genesis_block = Block(header=header, transactions=[coinbase_tx])

        # Sanity-check the block before writing.
        self.rules.validate_block(genesis_block, prev_block=None, height=0)

        self.blocks_store.write_block(genesis_block, 0)
        logger.info(
            "Synthetic regtest genesis written at height 0 (%s)",
            genesis_block.header.hash_hex()[:16],
        )
    
    def get_best_block_hash(self) -> Optional[str]:
        return self._best_hash
    
    def get_best_height(self) -> int:
        return self._best_height
    
    def get_best_chainwork(self) -> int:
        return self._best_chainwork
    
    def set_best_block(self, block_hash: str, height: int, chainwork: int) -> None:
        self._best_hash = block_hash
        self._best_height = height
        self._best_chainwork = chainwork
        self.block_index.set_best_chain_tip(block_hash)
        logger.info(f"New best block: {block_hash[:16]} at height {height}")
        
        # Notify wallet of new block
        if self._wallet_callback:
            try:
                self._wallet_callback()
            except Exception as e:
                logger.warning(f"Wallet callback failed: {e}")
    
    def set_wallet_callback(self, callback: callable) -> None:
        """Set callback to notify wallet when new blocks are connected."""
        self._wallet_callback = callback
    
    def get_block(self, block_hash: str) -> Optional[Block]:
        entry = self.block_index.get_block(block_hash)
        if entry:
            return self.blocks_store.read_block_by_hash(entry.block_hash)
        return None
    
    def get_block_by_height(self, height: int) -> Optional[Block]:
        entry = self.block_index.get_block_by_height(height)
        if entry:
            return self.blocks_store.read_block_by_hash(entry.block_hash)
        return None
    
    def get_header(self, height: int) -> Optional[BlockHeader]:
        entry = self.block_index.get_block_by_height(height)
        if not entry:
            return None
        return self.header_chain.get_header_by_hash(entry.block_hash)
    
    def get_header_by_hash(self, block_hash: str) -> Optional[BlockHeader]:
        return self.header_chain.get_header_by_hash(block_hash)
    
    def get_height(self, block_hash: str) -> Optional[int]:
        return self.header_chain.get_height(block_hash)
    
    def get_utxo(self, txid: str, index: int) -> Optional[Dict[str, Any]]:
        return self.utxo_store.get_utxo(txid, index)
    
    def get_balance(self, address: str) -> int:
        return self.utxo_store.get_balance(address)
    
    def get_utxos_for_address(self, address: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.utxo_store.get_utxos_for_address(address, limit)
    
    def transaction_exists(self, txid: str) -> bool:
        result = self.db.fetch_one("SELECT 1 FROM transactions WHERE txid = ?", (txid,))
        return result is not None
    
    def get_transaction(self, txid: str) -> Optional[Dict[str, Any]]:
        return self.db.fetch_one("""
            SELECT t.*, b.height as block_height, b.hash as block_hash
            FROM transactions t
            LEFT JOIN blocks b ON t.block_hash = b.hash
            WHERE t.txid = ?
        """, (txid,))
    
    def get_transaction_inputs(self, txid: str) -> List[Dict[str, Any]]:
        return self.db.fetch_all('SELECT * FROM inputs WHERE txid = ? ORDER BY "index"', (txid,))
    
    def get_transaction_outputs(self, txid: str) -> List[Dict[str, Any]]:
        return self.db.fetch_all('SELECT * FROM outputs WHERE txid = ? ORDER BY "index"', (txid,))
    
    def is_tx_confirmed(self, txid: str, min_conf: int = 1) -> bool:
        tx = self.get_transaction(txid)
        if not tx or not tx['block_height']:
            return False
        confirmations = self._best_height - tx['block_height'] + 1
        return confirmations >= min_conf
    
    def get_confirmations(self, txid: str) -> int:
        tx = self.get_transaction(txid)
        if not tx or not tx['block_height']:
            return 0
        return self._best_height - tx['block_height'] + 1
    
    def get_block_range(self, start_height: int, end_height: int) -> List[Block]:
        blocks = []
        for height in range(start_height, end_height + 1):
            block = self.get_block_by_height(height)
            if block:
                blocks.append(block)
        return blocks
    
    def get_headers_range(self, start_height: int, count: int) -> List[BlockHeader]:
        return self.header_chain.get_headers_range(start_height, count)
    
    def get_last_headers(self, count: int) -> List[BlockHeader]:
        return self.header_chain.get_last_headers(count)
    
    def is_fully_validated(self) -> bool:
        return self._best_height >= 0

    def validate_block_stateful(self, block: Block, height: int) -> bool:
        """Validate a block against current chainstate/UTXO state."""
        if self._block_validator is None:
            from .validation import BlockValidator
            self._block_validator = BlockValidator(self.params, self.utxo_store, self.block_index)
        return self._block_validator.validate_block(block, height)
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            'best_height': self._best_height,
            'best_hash': self._best_hash,
            'best_chainwork': self._best_chainwork,
            'utxo_count': self.utxo_store.get_utxo_count(),
            'utxo_value': self.utxo_store.get_total_value(),
            'block_index_size': self.block_index.size()
        }
