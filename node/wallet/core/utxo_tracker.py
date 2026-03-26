"""UTXO tracking for wallet."""

import time
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class UTXO:
    """Unspent Transaction Output."""
    txid: str
    vout: int
    amount: int
    address: str
    script_pubkey: bytes
    confirmations: int
    height: int
    is_coinbase: bool
    created_at: float = field(default_factory=time.time)
    spent: bool = False
    spent_by_txid: Optional[str] = None

class UTXOTracker:
    """UTXO tracking for wallet."""
    
    def __init__(self):
        """Initialize UTXO tracker."""
        self.utxos: Dict[str, Dict[int, UTXO]] = {}  # txid -> {vout: UTXO}
        self.address_utxos: Dict[str, List[UTXO]] = {}  # address -> list of UTXOs
        self.spent_utxos: Dict[str, Set[int]] = {}  # txid -> set of spent vouts
        
    def add_utxo(self, txid: str, vout: int, amount: int, address: str,
                 script_pubkey: bytes, height: int, is_coinbase: bool) -> None:
        """Add UTXO to tracker.
        
        Args:
            txid: Transaction ID
            vout: Output index
            amount: Amount in satoshis
            address: Address
            script_pubkey: Output script
            height: Block height
            is_coinbase: Whether from coinbase
        """
        utxo = UTXO(
            txid=txid,
            vout=vout,
            amount=amount,
            address=address,
            script_pubkey=script_pubkey,
            confirmations=0,
            height=height,
            is_coinbase=is_coinbase
        )
        
        # Store by txid
        if txid not in self.utxos:
            self.utxos[txid] = {}
        self.utxos[txid][vout] = utxo
        
        # Store by address
        if address not in self.address_utxos:
            self.address_utxos[address] = []
        self.address_utxos[address].append(utxo)
        
        logger.debug(f"Added UTXO: {txid[:16]}:{vout} = {amount} sat")
    
    def spend_utxo(self, txid: str, vout: int, spent_by_txid: str) -> bool:
        """Mark UTXO as spent.
        
        Args:
            txid: Transaction ID
            vout: Output index
            spent_by_txid: Transaction that spent this UTXO
        
        Returns:
            True if UTXO was found and marked spent
        """
        if txid in self.utxos and vout in self.utxos[txid]:
            utxo = self.utxos[txid][vout]
            utxo.spent = True
            utxo.spent_by_txid = spent_by_txid
            
            # Track spent
            if txid not in self.spent_utxos:
                self.spent_utxos[txid] = set()
            self.spent_utxos[txid].add(vout)
            
            logger.debug(f"Spent UTXO: {txid[:16]}:{vout} by {spent_by_txid[:16]}")
            return True
        
        return False
    
    def spend_utxos(self, utxos: List[Tuple[str, int]], spent_by_txid: str) -> None:
        """Spend multiple UTXOs.
        
        Args:
            utxos: List of (txid, vout) tuples
            spent_by_txid: Transaction that spent these UTXOs
        """
        for txid, vout in utxos:
            self.spend_utxo(txid, vout, spent_by_txid)
    
    def get_utxo(self, txid: str, vout: int) -> Optional[UTXO]:
        """Get UTXO by outpoint.
        
        Args:
            txid: Transaction ID
            vout: Output index
        
        Returns:
            UTXO or None
        """
        if txid in self.utxos:
            return self.utxos[txid].get(vout)
        return None
    
    def get_utxos_for_address(self, address: str, include_spent: bool = False) -> List[UTXO]:
        """Get UTXOs for address.
        
        Args:
            address: Address
            include_spent: Include spent UTXOs
        
        Returns:
            List of UTXOs
        """
        utxos = self.address_utxos.get(address, [])
        if not include_spent:
            utxos = [u for u in utxos if not u.spent]
        return utxos
    
    def get_utxos_for_account(self, account: str = None) -> List[UTXO]:
        """Get UTXOs for account - FIXED to return actual UTXOs."""
        all_utxos = []
        
        # Get all wallet addresses
        addresses = self.address_utxos.keys()
        
        for address in addresses:
            utxos = self.address_utxos.get(address, [])
            for utxo in utxos:
                if not utxo.spent:
                    all_utxos.append(utxo)
        
        return all_utxos
    
    def update_from_chain(self, chainstate, addresses: List[str]) -> None:
        """Update UTXOs from chain state - FIX to properly sync."""
        for address in addresses:
            # Get UTXOs from chain
            chain_utxos = chainstate.get_utxos_for_address(address, 1000)
            
            for utxo in chain_utxos:
                # Check if we already have this UTXO
                existing = self.get_utxo(utxo['txid'], utxo['index'])  # Use 'index' not 'vout'
                if not existing:
                    # Add new UTXO
                    self.add_utxo(
                        txid=utxo['txid'],
                        vout=utxo['index'],  # Use 'index' not 'vout'
                        amount=utxo['value'],
                        address=address,
                        script_pubkey=utxo['script_pubkey'],
                        height=utxo['height'],
                        is_coinbase=utxo.get('is_coinbase', False)
                    )
    
    def get_balance(self, address: Optional[str] = None) -> int:
        """Get balance.
        
        Args:
            address: Address (None for all)
        
        Returns:
            Balance in satoshis
        """
        if address:
            utxos = self.get_utxos_for_address(address, include_spent=False)
            return sum(u.amount for u in utxos)
        
        # All addresses
        total = 0
        for txid_dict in self.utxos.values():
            for utxo in txid_dict.values():
                if not utxo.spent:
                    total += utxo.amount
        
        return total
    
    def update_confirmations(self, current_height: int) -> None:
        """Update confirmations for all UTXOs.
        
        Args:
            current_height: Current block height
        """
        for txid_dict in self.utxos.values():
            for utxo in txid_dict.values():
                if utxo.height > 0:
                    utxo.confirmations = current_height - utxo.height + 1
    
    def cleanup_mature_coinbase(self, current_height: int, maturity: int = 100) -> None:
        """Clean up mature coinbase UTXOs (optional).
        
        Args:
            current_height: Current block height
            maturity: Coinbase maturity (default 100)
        """
        for txid, txid_dict in self.utxos.items():
            for vout, utxo in txid_dict.items():
                if utxo.is_coinbase and not utxo.spent:
                    if current_height - utxo.height >= maturity:
                        # Keep in tracker but could be pruned
                        pass
    
    def get_utxo_count(self) -> Dict[str, int]:
        """Get UTXO counts.
        
        Returns:
            Dictionary with counts
        """
        total = 0
        spent = 0
        for txid_dict in self.utxos.values():
            for utxo in txid_dict.values():
                total += 1
                if utxo.spent:
                    spent += 1
        
        return {
            'total': total,
            'spent': spent,
            'unspent': total - spent,
            'addresses': len(self.address_utxos)
        }
    
    def clear(self) -> None:
        """Clear all UTXOs."""
        self.utxos.clear()
        self.address_utxos.clear()
        self.spent_utxos.clear()
        logger.info("Cleared UTXO tracker")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get UTXO tracker statistics.
        
        Returns:
            Statistics dictionary
        """
        counts = self.get_utxo_count()
        return {
            'utxos': counts,
            'balance': self.get_balance(),
            'total_value': sum(u.amount for txid_dict in self.utxos.values() 
                              for u in txid_dict.values() if not u.spent)
        }
