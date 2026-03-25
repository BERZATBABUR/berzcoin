"""Coin selection algorithms."""

from typing import List, Tuple, Optional, Any
from dataclasses import dataclass
from shared.utils.logging import get_logger

logger = get_logger()

@dataclass
class CoinSelectionResult:
    """Result of coin selection."""
    selected: List[Tuple[str, int, int]]  # (txid, vout, amount)
    change: int
    total_selected: int
    fee: int
    effective_value: int

class CoinSelector:
    """Coin selection for transactions."""
    
    def __init__(self):
        """Initialize coin selector."""
        self.dust_threshold = 546  # satoshis
    
    def select_coins(self, utxos: List[Any], target: int, fee_rate: int = 1,
                     strategy: str = "optimal") -> Optional[CoinSelectionResult]:
        """Select coins to meet target amount.
        
        Args:
            utxos: List of UTXO objects
            target: Target amount in satoshis
            fee_rate: Fee rate in sat/vbyte
            strategy: Selection strategy (optimal, largest, smallest, knapsack)
        
        Returns:
            CoinSelectionResult or None
        """
        if not utxos:
            return None
        
        # Sort UTXOs by amount
        utxos_sorted = sorted(utxos, key=lambda u: u.amount)
        
        # Different strategies
        if strategy == "largest":
            return self._select_largest(utxos_sorted, target, fee_rate)
        elif strategy == "smallest":
            return self._select_smallest(utxos_sorted, target, fee_rate)
        elif strategy == "knapsack":
            return self._select_knapsack(utxos_sorted, target, fee_rate)
        else:  # optimal
            return self._select_optimal(utxos_sorted, target, fee_rate)
    
    def _select_largest(self, utxos: List[Any], target: int, fee_rate: int) -> Optional[CoinSelectionResult]:
        """Select largest UTXOs first.
        
        Args:
            utxos: Sorted UTXOs (ascending)
            target: Target amount
            fee_rate: Fee rate
        
        Returns:
            CoinSelectionResult or None
        """
        selected = []
        total = 0
        
        # Use largest UTXOs (iterate reverse)
        for utxo in reversed(utxos):
            selected.append((utxo.txid, utxo.vout, utxo.amount))
            total += utxo.amount
            
            if total >= target:
                break
        
        if total < target:
            return None
        
        return self._create_result(selected, target, fee_rate)
    
    def _select_smallest(self, utxos: List[Any], target: int, fee_rate: int) -> Optional[CoinSelectionResult]:
        """Select smallest UTXOs first (branch and bound).
        
        Args:
            utxos: Sorted UTXOs (ascending)
            target: Target amount
            fee_rate: Fee rate
        
        Returns:
            CoinSelectionResult or None
        """
        selected = []
        total = 0
        
        # Try smallest UTXOs first
        for utxo in utxos:
            if total + utxo.amount <= target + 10000:  # Allow some overhead
                selected.append((utxo.txid, utxo.vout, utxo.amount))
                total += utxo.amount
            
            if total >= target:
                break
        
        if total < target:
            # Fallback to largest selection
            return self._select_largest(utxos, target, fee_rate)
        
        return self._create_result(selected, target, fee_rate)
    
    def _select_knapsack(self, utxos: List[Any], target: int, fee_rate: int) -> Optional[CoinSelectionResult]:
        """Select coins using knapsack algorithm.
        
        Args:
            utxos: Sorted UTXOs (ascending)
            target: Target amount
            fee_rate: Fee rate
        
        Returns:
            CoinSelectionResult or None
        """
        # Simplified knapsack - find combination closest to target
        dp = [None] * (target + 10000)  # Dynamic programming table
        dp[0] = []
        
        for utxo in utxos:
            amount = utxo.amount
            for i in range(len(dp) - 1, -1, -1):
                if dp[i] is not None and i + amount < len(dp):
                    if dp[i + amount] is None or len(dp[i + amount]) > len(dp[i]) + 1:
                        dp[i + amount] = dp[i] + [(utxo.txid, utxo.vout, amount)]
        
        # Find best combination
        best_amount = target
        best_selection = None
        
        for amount in range(target, min(target + 10000, len(dp))):
            if dp[amount] is not None:
                best_amount = amount
                best_selection = dp[amount]
                break
        
        if best_selection is None:
            return self._select_largest(utxos, target, fee_rate)
        
        return self._create_result(best_selection, target, fee_rate)
    
    def _select_optimal(self, utxos: List[Any], target: int, fee_rate: int) -> Optional[CoinSelectionResult]:
        """Select optimal combination (try multiple strategies).
        
        Args:
            utxos: Sorted UTXOs
            target: Target amount
            fee_rate: Fee rate
        
        Returns:
            CoinSelectionResult or None
        """
        # Try knapsack first
        result = self._select_knapsack(utxos, target, fee_rate)
        if result and result.effective_value >= target:
            return result
        
        # Try smallest
        result = self._select_smallest(utxos, target, fee_rate)
        if result and result.effective_value >= target:
            return result
        
        # Try largest
        result = self._select_largest(utxos, target, fee_rate)
        if result and result.effective_value >= target:
            return result
        
        return None
    
    def _create_result(self, selected: List[Tuple[str, int, int]], target: int,
                       fee_rate: int) -> CoinSelectionResult:
        """Create result from selected UTXOs.
        
        Args:
            selected: List of (txid, vout, amount)
            target: Target amount
            fee_rate: Fee rate
        
        Returns:
            CoinSelectionResult
        """
        total_selected = sum(amount for _, _, amount in selected)
        
        # Estimate transaction size (simplified)
        tx_size = 10 + len(selected) * 150 + 2 * 34  # Inputs + outputs
        fee = tx_size * fee_rate
        
        effective_value = total_selected - fee
        change = effective_value - target
        
        # Don't create dust change
        if 0 < change < self.dust_threshold:
            change = 0
            fee += change  # Add to fee
        elif change < 0:
            change = 0
        
        return CoinSelectionResult(
            selected=selected,
            change=change,
            total_selected=total_selected,
            fee=fee,
            effective_value=effective_value
        )
    
    def calculate_fee(self, inputs: int, outputs: int, fee_rate: int) -> int:
        """Calculate transaction fee.
        
        Args:
            inputs: Number of inputs
            outputs: Number of outputs
            fee_rate: Fee rate in sat/vbyte
        
        Returns:
            Fee in satoshis
        """
        # Simplified: 150 bytes per input, 34 bytes per output, 10 bytes overhead
        tx_size = 10 + inputs * 150 + outputs * 34
        return tx_size * fee_rate
