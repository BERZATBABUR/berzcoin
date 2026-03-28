"""Core consensus rules for BerzCoin."""

from typing import Callable, Dict, List, Optional, Tuple
from datetime import datetime
from ..core.block import Block, BlockHeader
from ..core.transaction import Transaction
from ..utils.time import median_time_past, is_timestamp_valid
from .params import ConsensusParams

class ConsensusRules:
    """Consensus validation rules."""
    
    def __init__(
        self,
        params: ConsensusParams,
        output_value_lookup: Optional[Callable[[str, int], Optional[int]]] = None,
    ):
        self.params = params
        # Optional callback: resolve (txid, vout) -> output value.
        # When set, subsidy validation can account for transaction fees.
        self.output_value_lookup = output_value_lookup
    
    def validate_block_header(self, header: BlockHeader, prev_header: Optional[BlockHeader] = None) -> bool:
        if header.version < 1 or header.version > 0x20000000:
            raise ValueError(f"Invalid version: {header.version}")
        if not is_timestamp_valid(header.timestamp, 7200):
            raise ValueError(f"Invalid timestamp: {header.timestamp}")
        if prev_header:
            pass
        target = self.get_target(header.bits)
        if not header.is_valid_pow(target):
            raise ValueError(f"Proof of work failed: hash {header.hash_hex()}")
        return True
    
    def validate_block(self, block: Block, prev_block: Optional[Block] = None,
                       height: int = 0) -> bool:
        prev_header = prev_block.header if prev_block else None
        self.validate_block_header(block.header, prev_header)
        if len(block.transactions) == 0:
            raise ValueError("Block must include at least one transaction")

        if len(block.serialize(include_witness=False)) > self.params.max_block_size:
            raise ValueError("Block size exceeds limit")
        if block.weight() > self.params.max_block_weight:
            raise ValueError("Block weight exceeds limit")
        if not block.verify_merkle_root():
            raise ValueError("Invalid merkle root")

        coinbase = block.transactions[0]
        if not coinbase.is_coinbase():
            raise ValueError("First transaction must be coinbase")
        for tx in block.transactions[1:]:
            if tx.is_coinbase():
                raise ValueError("Only first transaction may be coinbase")

        if height >= self.params.bip34_height:
            self.validate_coinbase_height(coinbase, height)

        total_sigops = 0
        for tx in block.transactions:
            self.validate_transaction(tx, height)
            total_sigops += self.count_sigops(tx)

        if total_sigops > self.params.max_block_sigops:
            raise ValueError(f"Too many sigops: {total_sigops}")

        if not self.validate_subsidy(block, height):
            raise ValueError("Invalid subsidy")

        return True

    def validate_transaction(self, tx: Transaction, height: int = 0) -> bool:
        if len(tx.vin) == 0 or len(tx.vout) == 0:
            raise ValueError("Empty transaction")

        total_out = 0
        for txout in tx.vout:
            if txout.value < 0:
                raise ValueError(f"Negative output value: {txout.value}")
            total_out += txout.value

        max_money = int(getattr(self.params, "max_money", 21_000_000 * 100_000_000))
        if total_out > max_money:
            raise ValueError("Total output exceeds max supply")

        if tx.is_coinbase():
            if len(tx.vin) != 1:
                raise ValueError("Coinbase must have exactly one input")
            if len(tx.vin[0].script_sig) < 2 or len(tx.vin[0].script_sig) > 100:
                raise ValueError("Invalid coinbase script length")
        else:
            for txin in tx.vin:
                if txin.prev_tx_hash == b'\x00' * 32:
                    raise ValueError("Non-coinbase input with zero prev_tx_hash")

        return True

    def validate_coinbase_height(self, coinbase: Transaction, height: int) -> bool:
        height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
        script = coinbase.vin[0].script_sig
        if len(script) == 0:
            raise ValueError("Empty coinbase script")
        if script[0] != len(height_bytes):
            if height_bytes not in script:
                raise ValueError(f"Coinbase height {height} not found in script")
        return True

    def count_sigops(self, tx: Transaction) -> int:
        sigops = 0
        for txin in tx.vin:
            for byte in txin.script_sig:
                if byte == 0xac or byte == 0xad:
                    sigops += 1
                elif byte == 0xae or byte == 0xaf:
                    sigops += 20
        for txout in tx.vout:
            for byte in txout.script_pubkey:
                if byte == 0xac or byte == 0xad:
                    sigops += 1
                elif byte == 0xae or byte == 0xaf:
                    sigops += 20
        return sigops

    def validate_subsidy(self, block: Block, height: int) -> bool:
        from .subsidy import get_block_subsidy
        expected_subsidy = get_block_subsidy(height, self.params)
        coinbase = block.transactions[0]
        actual_subsidy = sum(txout.value for txout in coinbase.vout)
        total_fees = self.get_total_fees(block)
        if total_fees < 0:
            return False
        if actual_subsidy > expected_subsidy + total_fees:
            return False
        return True

    def get_total_fees(self, block: Block) -> int:
        # Resolve in-block dependency spends first (parent tx already processed in
        # this block), then fallback to chain/output lookup callback.
        total_fees = 0
        in_block_outputs: Dict[Tuple[str, int], int] = {}

        for tx in block.transactions[1:]:
            input_total = 0
            for txin in tx.vin:
                prev_txid = txin.prev_tx_hash.hex()
                prev_index = int(txin.prev_tx_index)
                key = (prev_txid, prev_index)

                if key in in_block_outputs:
                    value = in_block_outputs[key]
                elif self.output_value_lookup is not None:
                    resolved = self.output_value_lookup(prev_txid, prev_index)
                    if resolved is None:
                        return -1
                    value = int(resolved)
                else:
                    return -1

                if value < 0:
                    return -1
                input_total += value

            output_total = sum(int(txout.value) for txout in tx.vout)
            fee = input_total - output_total
            if fee < 0:
                return -1
            total_fees += fee

            txid = tx.txid().hex()
            for idx, txout in enumerate(tx.vout):
                in_block_outputs[(txid, idx)] = int(txout.value)

        return total_fees

    def get_target(self, bits: int) -> int:
        exponent = bits >> 24
        coefficient = bits & 0x007fffff
        if exponent <= 3:
            target = coefficient >> (8 * (3 - exponent))
        else:
            target = coefficient << (8 * (exponent - 3))
        if target < 0 or target > self.params.pow_limit:
            target = self.params.pow_limit
        return target

    def get_bits(self, target: int) -> int:
        target_bytes = target.to_bytes(32, 'big')
        for i, byte in enumerate(target_bytes):
            if byte != 0:
                break
        exponent = 32 - i
        coefficient = int.from_bytes(target_bytes[i:i+3], 'big')
        if coefficient >= 0x800000:
            coefficient >>= 8
            exponent += 1
        return (exponent << 24) | coefficient
