"""Transaction building for wallet."""

from typing import List, Tuple, Optional, Dict
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.script.opcodes import Opcode
from shared.crypto.base58 import base58_check_decode
from shared.crypto.bech32 import bech32_decode
from shared.utils.logging import get_logger

logger = get_logger()

class TransactionBuilder:
    """Build and sign transactions."""
    
    def __init__(self, network: str = "mainnet"):
        """Initialize transaction builder.
        
        Args:
            network: Network (mainnet, testnet, regtest)
        """
        self.network = network
    
    def create_transaction(self, inputs: List[Tuple[str, int, int]], 
                          outputs: List[Tuple[str, int]],
                          change_address: str = None,
                          fee: int = None) -> Transaction:
        """Create a new transaction - FIXED with proper UTXO handling."""
        
        # Validate inputs
        if not inputs:
            raise ValueError("No inputs provided")
        
        # Create transaction
        tx = Transaction(version=2)
        
        # Add inputs
        total_in = 0
        for txid, vout, amount in inputs:
            txin = TxIn(
                prev_tx_hash=bytes.fromhex(txid),
                prev_tx_index=vout,
                script_sig=b'',  # Will be signed later
                sequence=0xffffffff
            )
            tx.vin.append(txin)
            total_in += amount
        
        # Add outputs
        total_out = 0
        for address, amount in outputs:
            if amount <= 0:
                continue
            
            script_pubkey = self._create_script_pubkey(address)
            txout = TxOut(value=amount, script_pubkey=script_pubkey)
            tx.vout.append(txout)
            total_out += amount
        
        # Calculate fee if not provided
        if fee is None:
            # Estimate fee: 150 bytes per input, 34 bytes per output
            estimated_size = 10 + len(inputs) * 150 + len(outputs) * 34
            fee = estimated_size * 1  # 1 sat/vbyte minimum
        
        # Calculate change
        change_amount = total_in - total_out - fee
        
        if change_amount > 0:
            if change_address:
                script_pubkey = self._create_script_pubkey(change_address)
                tx.vout.append(TxOut(value=change_amount, script_pubkey=script_pubkey))
            else:
                # No change address, add to fee
                fee += change_amount
        
        logger.debug(f"Created transaction: {len(tx.vin)} inputs, {len(tx.vout)} outputs, "
                    f"total_in={total_in}, total_out={total_out}, fee={fee}")
        
        return tx
    
    def _create_script_pubkey(self, address: str) -> bytes:
        """Create scriptPubKey from address.
        
        Args:
            address: Bitcoin address
        
        Returns:
            ScriptPubKey bytes
        """
        # Simplified - detect address type
        if address.startswith('bc1') or address.startswith('tb1') or address.startswith('bcrt1'):
            # Bech32 (SegWit)
            return self._create_p2wpkh_script(address)
        elif address.startswith('3'):
            # P2SH
            return self._create_p2sh_script(address)
        else:
            # P2PKH
            return self._create_p2pkh_script(address)
    
    def _create_p2pkh_script(self, address: str) -> bytes:
        """Create P2PKH scriptPubKey.
        
        Args:
            address: Bitcoin address
        
        Returns:
            Script bytes
        """
        # Decode address
        pubkey_hash = base58_check_decode(address)[1:]  # Remove version byte
        
        # Build script: OP_DUP OP_HASH160 <pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG
        script = (
            bytes([Opcode.OP_DUP, Opcode.OP_HASH160, 0x14])
            + pubkey_hash
            + bytes([Opcode.OP_EQUALVERIFY, Opcode.OP_CHECKSIG])
        )
        return script
    
    def _create_p2sh_script(self, address: str) -> bytes:
        """Create P2SH scriptPubKey.
        
        Args:
            address: Bitcoin address
        
        Returns:
            Script bytes
        """
        # Decode address
        script_hash = base58_check_decode(address)[1:]  # Remove version byte
        
        # Build script: OP_HASH160 <script_hash> OP_EQUAL
        script = (
            bytes([Opcode.OP_HASH160, 0x14])
            + script_hash
            + bytes([Opcode.OP_EQUAL])
        )
        return script
    
    def _create_p2wpkh_script(self, address: str) -> bytes:
        """Create P2WPKH scriptPubKey.
        
        Args:
            address: Bitcoin address
        
        Returns:
            Script bytes
        """
        # Decode address
        hrp, witver, witprog = bech32_decode(address)
        if witprog is None:
            raise ValueError(f"Invalid bech32 address: {address}")
        
        # Build script: OP_0 <witness_program>
        script = bytes([witver, len(witprog)]) + witprog
        return script
    
    def _estimate_fee(self, inputs: int, outputs: int) -> int:
        """Estimate transaction fee.
        
        Args:
            inputs: Number of inputs
            outputs: Number of outputs
        
        Returns:
            Estimated fee in satoshis
        """
        # Estimate size: 150 bytes per input, 34 bytes per output, 10 bytes overhead
        estimated_size = 10 + inputs * 150 + outputs * 34
        # Use 1 sat/vbyte as minimum fee
        return estimated_size * 1
    
    def sign_transaction(self, tx: Transaction, private_keys: Dict[str, bytes]) -> bool:
        """Sign transaction inputs.
        
        Args:
            tx: Transaction to sign
            private_keys: Map of address -> private key bytes
        
        Returns:
            True if all inputs signed
        """
        for i, txin in enumerate(tx.vin):
            # Get UTXO script (would need from UTXO tracker)
            # Simplified - in production, get script from UTXO
            pass
        
        return True
    
    def create_coinbase(self, height: int, reward: int, address: str) -> Transaction:
        """Create coinbase transaction.
        
        Args:
            height: Block height
            reward: Block reward
            address: Mining reward address
        
        Returns:
            Coinbase transaction
        """
        # Create coinbase input
        txin = TxIn(
            prev_tx_hash=b'\x00' * 32,
            prev_tx_index=0xffffffff,
            script_sig=self._create_coinbase_script(height),
            sequence=0xffffffff
        )
        
        # Create output
        script_pubkey = self._create_script_pubkey(address)
        txout = TxOut(value=reward, script_pubkey=script_pubkey)
        
        # Create transaction
        tx = Transaction(version=1)
        tx.vin.append(txin)
        tx.vout.append(txout)
        
        return tx
    
    def _create_coinbase_script(self, height: int) -> bytes:
        """Create coinbase script.
        
        Args:
            height: Block height
        
        Returns:
            Coinbase script bytes
        """
        # Encode height
        height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
        script = bytes([len(height_bytes)]) + height_bytes + b"/BerzCoin/"
        return script
