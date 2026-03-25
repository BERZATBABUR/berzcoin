"""Block validation logic."""

from typing import Optional, List
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction
from shared.consensus.params import ConsensusParams
from shared.consensus.rules import ConsensusRules
from shared.consensus.pow import ProofOfWork
from shared.consensus.weights import calculate_block_weight
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from .block_index import BlockIndex, BlockIndexEntry
from .chainwork import ChainWork

logger = get_logger()

class BlockValidator:
    def __init__(self, params: ConsensusParams, utxo_store: UTXOStore,
                 block_index: BlockIndex):
        self.params = params
        self.utxo_store = utxo_store
        self.block_index = block_index
        self.rules = ConsensusRules(params)
        self.pow = ProofOfWork(params)
        self.chainwork = ChainWork(params)

    def validate_block(self, block: Block, height: int, is_orphan: bool = False) -> bool:
        if not self.validate_header(block.header, height):
            return False
        if not self.validate_size(block):
            return False
        if not block.verify_merkle_root():
            logger.error(f"Invalid merkle root for block {height}")
            return False
        if not self.validate_transactions(block, height):
            return False
        if not self.validate_coinbase(block.transactions[0], height):
            return False
        if not self.validate_subsidy(block, height):
            return False
        if not self.validate_sigops(block):
            return False
        if not self.validate_timestamps(block, height):
            return False
        logger.debug(f"Block {height} validated successfully")
        return True

    def validate_header(self, header: BlockHeader, height: int) -> bool:
        if header.version < 1 or header.version > 0x20000000:
            logger.error(f"Invalid version: {header.version}")
            return False
        if not self.pow.validate(header):
            logger.error(f"Proof of work failed for block {height}")
            return False
        if header.timestamp > 0x7fffffff:
            logger.error(f"Timestamp too large: {header.timestamp}")
            return False
        if height > 0:
            prev_hash = header.prev_block_hash.hex()
            if not self.block_index.get_block(prev_hash):
                logger.error(f"Previous block not found: {prev_hash}")
                return False
        return True

    def validate_size(self, block: Block) -> bool:
        block_size = block.size()
        if block_size > self.params.max_block_size:
            logger.error(f"Block size {block_size} exceeds limit {self.params.max_block_size}")
            return False
        block_weight = calculate_block_weight(block)
        if block_weight > self.params.max_block_weight:
            logger.error(f"Block weight {block_weight} exceeds limit {self.params.max_block_weight}")
            return False
        return True

    def validate_transactions(self, block: Block, height: int) -> bool:
        for i, tx in enumerate(block.transactions):
            if not self.validate_transaction(tx, height, i == 0):
                logger.error(f"Invalid transaction {i} in block {height}")
                return False
        txids = set()
        for tx in block.transactions:
            txid = tx.txid().hex()
            if txid in txids:
                logger.error(f"Duplicate transaction {txid} in block")
                return False
            txids.add(txid)
        return True

    def validate_transaction(self, tx: Transaction, height: int, is_coinbase: bool) -> bool:
        if tx.is_coinbase() != is_coinbase:
            logger.error("Invalid coinbase status")
            return False
        if not is_coinbase:
            for txin in tx.vin:
                utxo = self.utxo_store.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if not utxo:
                    logger.error(f"UTXO not found: {txin.prev_tx_hash.hex()}:{txin.prev_tx_index}")
                    return False
                if utxo['is_coinbase'] and height - utxo['height'] < 100:
                    logger.error("Coinbase UTXO not mature")
                    return False
        total_out = 0
        for txout in tx.vout:
            if txout.value < 0:
                logger.error(f"Negative output value: {txout.value}")
                return False
            total_out += txout.value
            if txout.value < 546 and len(txout.script_pubkey) > 0:
                if txout.script_pubkey[0] not in [0x6a]:
                    logger.warning(f"Dust output: {txout.value} satoshis")
        if total_out > 21000000 * 100000000:
            logger.error("Total output exceeds max supply")
            return False
        return True

    def validate_coinbase(self, coinbase: Transaction, height: int) -> bool:
        if len(coinbase.vin) != 1:
            logger.error(f"Coinbase has {len(coinbase.vin)} inputs")
            return False
        script_len = len(coinbase.vin[0].script_sig)
        if script_len < 2 or script_len > 100:
            logger.error(f"Invalid coinbase script length: {script_len}")
            return False
        if height >= self.params.bip34_height:
            height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
            script = coinbase.vin[0].script_sig
            if len(script) < len(height_bytes):
                logger.error("Coinbase script too short for height")
                return False
            if script[0] != len(height_bytes):
                logger.warning("Coinbase height not properly encoded")
        return True

    def validate_subsidy(self, block: Block, height: int) -> bool:
        from shared.consensus.subsidy import get_block_subsidy
        expected_subsidy = get_block_subsidy(height, self.params)
        coinbase = block.transactions[0]
        total_out = sum(txout.value for txout in coinbase.vout)
        total_fees = self._calculate_fees(block)
        if total_out > expected_subsidy + total_fees:
            logger.error(f"Coinbase output {total_out} exceeds subsidy + fees {expected_subsidy + total_fees}")
            return False
        return True

    def validate_sigops(self, block: Block) -> bool:
        total_sigops = 0
        for tx in block.transactions:
            total_sigops += self.rules.count_sigops(tx)
        if total_sigops > self.params.max_block_sigops:
            logger.error(f"Sigops {total_sigops} exceeds limit {self.params.max_block_sigops}")
            return False
        return True

    def validate_timestamps(self, block: Block, height: int) -> bool:
        median_time = self._get_median_time_past(height)
        if block.header.timestamp <= median_time:
            logger.error(f"Block timestamp {block.header.timestamp} <= median time {median_time}")
            return False
        import time
        if block.header.timestamp > time.time() + 7200:
            logger.error(f"Block timestamp too far in future: {block.header.timestamp}")
            return False
        return True

    def _calculate_fees(self, block: Block) -> int:
        total_input = 0
        total_output = 0
        for tx in block.transactions[1:]:
            for txin in tx.vin:
                utxo = self.utxo_store.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if utxo:
                    total_input += utxo['value']
            for txout in tx.vout:
                total_output += txout.value
        return total_input - total_output

    def _get_median_time_past(self, height: int) -> int:
        times = []
        for h in range(max(0, height - 11), height):
            entry = self.block_index.get_block_by_height(h)
            if entry:
                times.append(entry.header.timestamp)
        if not times:
            return 0
        times.sort()
        return times[len(times) // 2]
