"""Block structure for BerzCoin."""

from typing import List, Optional, Tuple
from datetime import datetime
from .transaction import Transaction
from .hashes import hash256
from .serialization import Serializer
from ..utils.time import current_time

class BlockHeader:
    """Bitcoin block header."""
    
    def __init__(self, version: int = 1, prev_block_hash: bytes = b'\x00' * 32,
                 merkle_root: bytes = b'\x00' * 32, timestamp: Optional[int] = None,
                 bits: int = 0x1d00ffff, nonce: int = 0):
        """Initialize block header.
        
        Args:
            version: Block version
            prev_block_hash: Previous block hash (32 bytes)
            merkle_root: Merkle root of transactions (32 bytes)
            timestamp: Block timestamp (Unix time)
            bits: Difficulty target
            nonce: Proof of work nonce
        """
        self.version = version
        self.prev_block_hash = prev_block_hash
        self.merkle_root = merkle_root
        self.timestamp = timestamp if timestamp is not None else current_time()
        self.bits = bits
        self.nonce = nonce
    
    def serialize(self) -> bytes:
        """Serialize block header.
        
        Returns:
            Serialized header bytes (80 bytes)
        """
        result = Serializer.write_uint32(self.version)
        result += self.prev_block_hash
        result += self.merkle_root
        result += Serializer.write_uint32(self.timestamp)
        result += Serializer.write_uint32(self.bits)
        result += Serializer.write_uint32(self.nonce)
        return result
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['BlockHeader', int]:
        """Deserialize block header.
        
        Args:
            data: Source bytes
            offset: Starting offset
        
        Returns:
            Tuple of (BlockHeader, new offset)
        """
        version, offset = Serializer.read_uint32(data, offset)
        prev_block_hash, offset = Serializer.read_bytes(data, offset, 32)
        merkle_root, offset = Serializer.read_bytes(data, offset, 32)
        timestamp, offset = Serializer.read_uint32(data, offset)
        bits, offset = Serializer.read_uint32(data, offset)
        nonce, offset = Serializer.read_uint32(data, offset)
        
        return cls(version, prev_block_hash, merkle_root, timestamp, bits, nonce), offset
    
    def hash(self) -> bytes:
        """Calculate block hash (double SHA256).
        
        Returns:
            Block hash (32 bytes)
        """
        return hash256(self.serialize())
    
    def hash_hex(self) -> str:
        """Get block hash as hex string.
        
        Returns:
            Block hash in hex (little-endian display)
        """
        return self.hash()[::-1].hex()
    
    def is_valid_pow(self, target: int) -> bool:
        """Check if block hash meets difficulty target.
        
        Args:
            target: Difficulty target
        
        Returns:
            True if proof of work is valid
        """
        block_hash_int = int.from_bytes(self.hash(), 'little')
        return block_hash_int <= target
    
    def __repr__(self) -> str:
        """String representation."""
        return f"BlockHeader(version={self.version}, hash={self.hash_hex()[:16]}..., nonce={self.nonce})"

class Block:
    """Bitcoin block."""
    
    def __init__(self, header: BlockHeader, transactions: List[Transaction]):
        """Initialize block.
        
        Args:
            header: Block header
            transactions: List of transactions
        """
        self.header = header
        self.transactions = transactions
    
    def serialize(self, include_witness: bool = True) -> bytes:
        """Serialize block.
        
        Args:
            include_witness: Include witness data for SegWit transactions
        
        Returns:
            Serialized block bytes
        """
        result = self.header.serialize()
        result += Serializer.write_varint(len(self.transactions))
        
        for tx in self.transactions:
            result += tx.serialize(include_witness=include_witness)
        
        return result
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['Block', int]:
        """Deserialize block.
        
        Args:
            data: Source bytes
            offset: Starting offset
        
        Returns:
            Tuple of (Block, new offset)
        """
        header, offset = BlockHeader.deserialize(data, offset)
        
        n_transactions, offset = Serializer.read_varint(data, offset)
        transactions = []
        
        for _ in range(n_transactions):
            tx, offset = Transaction.deserialize(data, offset)
            transactions.append(tx)
        
        return cls(header, transactions), offset
    
    def calculate_merkle_root(self) -> bytes:
        """Calculate merkle root from transactions.
        
        Returns:
            Merkle root (32 bytes)
        """
        from .merkle import merkle_root
        
        if not self.transactions:
            return b'\x00' * 32
        
        tx_hashes = [tx.txid() for tx in self.transactions]
        root = merkle_root(tx_hashes)
        return root if root is not None else b'\x00' * 32
    
    def verify_merkle_root(self) -> bool:
        """Verify that header merkle root matches transactions.
        
        Returns:
            True if merkle root is valid
        """
        return self.calculate_merkle_root() == self.header.merkle_root
    
    def is_valid(self) -> bool:
        """Basic block validation.
        
        Returns:
            True if block passes basic validation
        """
        # Must have at least one transaction (coinbase)
        if len(self.transactions) == 0:
            return False
        
        # First transaction must be coinbase
        if not self.transactions[0].is_coinbase():
            return False
        
        # No other coinbase transactions
        for tx in self.transactions[1:]:
            if tx.is_coinbase():
                return False
        
        # Verify merkle root
        if not self.verify_merkle_root():
            return False
        
        return True
    
    def weight(self) -> int:
        """Calculate block weight (SegWit).
        
        Returns:
            Block weight
        """
        # The weight algorithm lives in ``shared/consensus/weights.py``.
        # (This module is in ``shared/core`` so relative import would be wrong.)
        from shared.consensus.weights import calculate_block_weight
        return calculate_block_weight(self)
    
    def size(self) -> int:
        """Calculate block size in bytes (without witness).
        
        Returns:
            Block size
        """
        return len(self.serialize(include_witness=False))
    
    def base_size(self) -> int:
        """Calculate base size (without witness).
        
        Returns:
            Base size
        """
        return self.size()
    
    def total_size(self) -> int:
        """Calculate total size with witness.
        
        Returns:
            Total size
        """
        return len(self.serialize(include_witness=True))
    
    def __repr__(self) -> str:
        """String representation."""
        return f"Block(hash={self.header.hash_hex()[:16]}..., txs={len(self.transactions)})"
