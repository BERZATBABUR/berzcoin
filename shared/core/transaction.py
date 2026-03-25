"""Transaction structure for BerzCoin."""

from typing import List, Optional, Tuple
from ..script.witness import Witness
from ..core.serialization import Serializer
from ..core.hashes import hash256
from ..utils.errors import SerializationError

class TxIn:
    """Transaction input."""
    
    def __init__(self, prev_tx_hash: bytes = b'\x00' * 32, prev_tx_index: int = 0xffffffff,
                 script_sig: bytes = b'', sequence: int = 0xffffffff, witness: Optional[Witness] = None):
        """Initialize transaction input.
        
        Args:
            prev_tx_hash: Previous transaction hash (32 bytes)
            prev_tx_index: Previous transaction output index
            script_sig: Input script
            sequence: Sequence number
            witness: Witness data for SegWit
        """
        self.prev_tx_hash = prev_tx_hash
        self.prev_tx_index = prev_tx_index
        self.script_sig = script_sig
        self.sequence = sequence
        self.witness = witness or Witness()
    
    def is_coinbase(self) -> bool:
        """Check if this is a coinbase input."""
        return self.prev_tx_hash == b'\x00' * 32 and self.prev_tx_index == 0xffffffff
    
    def serialize(self, include_witness: bool = False) -> bytes:
        """Serialize transaction input.
        
        Args:
            include_witness: Include witness data
        
        Returns:
            Serialized input bytes
        """
        result = self.prev_tx_hash
        result += Serializer.write_uint32(self.prev_tx_index)
        result += Serializer.write_bytes(self.script_sig)
        result += Serializer.write_uint32(self.sequence)
        
        if include_witness and not self.witness.is_empty():
            result += self.witness.serialize()
        
        return result
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0, has_witness: bool = False) -> Tuple['TxIn', int]:
        """Deserialize transaction input.
        
        Args:
            data: Source bytes
            offset: Starting offset
            has_witness: Whether to expect witness data
        
        Returns:
            Tuple of (TxIn, new offset)
        """
        # Read prev_tx_hash
        prev_tx_hash, offset = Serializer.read_bytes(data, offset, 32)
        
        # Read prev_tx_index
        prev_tx_index, offset = Serializer.read_uint32(data, offset)
        
        # Read script_sig
        sig_len, offset = Serializer.read_varint(data, offset)
        script_sig, offset = Serializer.read_bytes(data, offset, sig_len)
        
        # Read sequence
        sequence, offset = Serializer.read_uint32(data, offset)
        
        witness = Witness()
        if has_witness:
            witness, offset = Witness.deserialize(data, offset)
        
        return cls(prev_tx_hash, prev_tx_index, script_sig, sequence, witness), offset
    
    def __repr__(self) -> str:
        """String representation."""
        prev_hash = self.prev_tx_hash.hex()[:16]
        return f"TxIn(prev={prev_hash}..., index={self.prev_tx_index})"

class TxOut:
    """Transaction output."""
    
    def __init__(self, value: int, script_pubkey: bytes):
        """Initialize transaction output.
        
        Args:
            value: Amount in satoshis
            script_pubkey: Output script
        """
        self.value = value
        self.script_pubkey = script_pubkey
    
    def serialize(self) -> bytes:
        """Serialize transaction output.
        
        Returns:
            Serialized output bytes
        """
        result = Serializer.write_uint64(self.value)
        result += Serializer.write_bytes(self.script_pubkey)
        return result
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['TxOut', int]:
        """Deserialize transaction output.
        
        Args:
            data: Source bytes
            offset: Starting offset
        
        Returns:
            Tuple of (TxOut, new offset)
        """
        value, offset = Serializer.read_uint64(data, offset)
        spk_len, offset = Serializer.read_varint(data, offset)
        script_pubkey, offset = Serializer.read_bytes(data, offset, spk_len)
        return cls(value, script_pubkey), offset
    
    def __repr__(self) -> str:
        """String representation."""
        return f"TxOut(value={self.value}, script={self.script_pubkey.hex()[:16]}...)"

class Transaction:
    """Bitcoin transaction."""
    
    def __init__(self, version: int = 1, inputs: Optional[List[TxIn]] = None,
                 outputs: Optional[List[TxOut]] = None, locktime: int = 0):
        """Initialize transaction.
        
        Args:
            version: Transaction version
            inputs: List of inputs
            outputs: List of outputs
            locktime: Locktime
        """
        self.version = version
        self.vin = inputs or []
        self.vout = outputs or []
        self.locktime = locktime
    
    def has_witness(self) -> bool:
        """Check if transaction has witness data."""
        return any(txin.witness and not txin.witness.is_empty() for txin in self.vin)
    
    def serialize(self, include_witness: bool = True) -> bytes:
        """Serialize transaction.
        
        Args:
            include_witness: Include witness data
        
        Returns:
            Serialized transaction bytes
        """
        result = Serializer.write_uint32(self.version)
        
        has_witness = include_witness and self.has_witness()
        if has_witness:
            result += b'\x00\x01'  # Marker and flag for SegWit
        
        # Serialize inputs
        result += Serializer.write_varint(len(self.vin))
        for txin in self.vin:
            result += txin.serialize(include_witness=has_witness)
        
        # Serialize outputs
        result += Serializer.write_varint(len(self.vout))
        for txout in self.vout:
            result += txout.serialize()
        
        if has_witness:
            # Witness data already included in inputs
            pass
        
        result += Serializer.write_uint32(self.locktime)
        return result
    
    @classmethod
    def deserialize(cls, data: bytes, offset: int = 0) -> Tuple['Transaction', int]:
        """Deserialize transaction.
        
        Args:
            data: Source bytes
            offset: Starting offset
        
        Returns:
            Tuple of (Transaction, new offset)
        """
        # Read version
        version, offset = Serializer.read_uint32(data, offset)
        
        # Check for SegWit marker
        has_witness = False
        if offset < len(data):
            marker = data[offset]
            if marker == 0x00:
                offset += 1
                flag = data[offset]
                offset += 1
                if flag == 0x01:
                    has_witness = True
        
        # Read inputs
        n_inputs, offset = Serializer.read_varint(data, offset)
        inputs = []
        for _ in range(n_inputs):
            txin, offset = TxIn.deserialize(data, offset, has_witness)
            inputs.append(txin)
        
        # Read outputs
        n_outputs, offset = Serializer.read_varint(data, offset)
        outputs = []
        for _ in range(n_outputs):
            txout, offset = TxOut.deserialize(data, offset)
            outputs.append(txout)
        
        # Read locktime
        locktime, offset = Serializer.read_uint32(data, offset)
        
        return cls(version, inputs, outputs, locktime), offset
    
    def txid(self) -> bytes:
        """Calculate transaction ID (double SHA256 of serialized transaction without witness).
        
        Returns:
            Transaction ID (32 bytes)
        """
        return hash256(self.serialize(include_witness=False))
    
    def wtxid(self) -> bytes:
        """Calculate witness transaction ID (double SHA256 of serialized transaction with witness).
        
        Returns:
            Witness transaction ID (32 bytes)
        """
        return hash256(self.serialize(include_witness=True))
    
    def is_coinbase(self) -> bool:
        """Check if transaction is coinbase."""
        return len(self.vin) == 1 and self.vin[0].is_coinbase()
    
    def total_out(self) -> int:
        """Calculate total output value.
        
        Returns:
            Total value in satoshis
        """
        return sum(txout.value for txout in self.vout)
    
    def __repr__(self) -> str:
        """String representation."""
        txid = self.txid().hex()[:16]
        return f"Transaction(txid={txid}..., ins={len(self.vin)}, outs={len(self.vout)})"
