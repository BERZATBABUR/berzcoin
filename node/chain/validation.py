"""Block validation logic."""

from typing import Optional, List
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction
from shared.consensus.buried_deployments import (
    HARDFORK_TX_V2,
    SOFTFORK_BIP34_STRICT,
    is_consensus_feature_active,
)
from shared.consensus.params import ConsensusParams
from shared.consensus.rules import ConsensusRules
from shared.consensus.pow import ProofOfWork
from shared.consensus.weights import calculate_block_weight
from shared.script.verify import verify_input_script
from shared.utils.logging import get_logger
from node.storage.utxo_store import UTXOStore
from node.validation.limits import ValidationLimits
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
        self.limits = ValidationLimits.from_params(params)
        self.coinbase_maturity = self.limits.coinbase_maturity

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
        # Allow BIP9-style versionbits in top-bit "001" namespace.
        if header.version < 1 or header.version > 0x3fffffff:
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
            prev_entry = self.block_index.get_block(prev_hash)
            if not prev_entry:
                logger.error(f"Previous block not found: {prev_hash}")
                return False
            expected_bits = self._expected_bits_for_height(height, prev_entry)
            if expected_bits is not None and int(header.bits) != int(expected_bits):
                logger.error(
                    "Unexpected difficulty bits at height %s: got=%s expected=%s",
                    height,
                    hex(int(header.bits)),
                    hex(int(expected_bits)),
                )
                return False
        return True

    def _expected_bits_for_height(self, height: int, prev_entry: BlockIndexEntry) -> Optional[int]:
        """Return expected compact target bits for this height when determinable."""
        if height <= 0:
            return None

        # When retargeting is disabled, next block keeps parent bits.
        if self.params.pow_no_retargeting:
            return int(prev_entry.header.bits)

        interval = max(1, int(self.params.retarget_interval_blocks()))
        if height % interval != 0:
            return int(prev_entry.header.bits)

        # Need the full prior interval window to deterministically retarget.
        if not hasattr(self.block_index, "get_block_by_height"):
            return None
        start_height = height - interval
        if start_height < 0:
            return int(prev_entry.header.bits)

        headers: List[BlockHeader] = []
        for h in range(start_height, height):
            ent = self.block_index.get_block_by_height(h)
            if ent is None or getattr(ent, "header", None) is None:
                return None
            headers.append(ent.header)

        if not headers:
            return int(prev_entry.header.bits)
        return int(self.pow.get_next_work_required(headers, height - 1))

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
        if len(block.transactions) == 0:
            logger.error("Block contains no transactions")
            return False

        # Consensus rule: exactly one coinbase and it must be first.
        coinbase_count = sum(1 for tx in block.transactions if tx.is_coinbase())
        if coinbase_count != 1:
            logger.error("Invalid coinbase count in block: %s", coinbase_count)
            return False

        # Consensus rule: no double-spend across the whole block.
        block_spent = set()
        for i, tx in enumerate(block.transactions):
            if not self.validate_transaction(tx, height, i == 0):
                logger.error(f"Invalid transaction {i} in block {height}")
                return False
            if i > 0:
                for txin in tx.vin:
                    outpoint = (txin.prev_tx_hash.hex(), int(txin.prev_tx_index))
                    if outpoint in block_spent:
                        logger.error("Block-level double spend detected")
                        return False
                    block_spent.add(outpoint)
        txids = set()
        for tx in block.transactions:
            txid = tx.txid().hex()
            if txid in txids:
                logger.error(f"Duplicate transaction {txid} in block")
                return False
            txids.add(txid)
        return True

    def validate_transaction(self, tx: Transaction, height: int, is_coinbase: bool) -> bool:
        if is_consensus_feature_active(self.params, HARDFORK_TX_V2, height):
            if int(getattr(tx, "version", 1)) < 2:
                logger.error("Transaction version below hardfork_v2 minimum")
                return False

        if tx.is_coinbase() != is_coinbase:
            logger.error("Invalid coinbase status")
            return False
        if not tx.vin or not tx.vout:
            logger.error("Transaction missing inputs or outputs")
            return False
        tx_size = tx.size()
        if tx_size <= 0 or tx_size > self.params.max_block_size:
            logger.error("Invalid transaction size: %s", tx_size)
            return False
        seen_inputs = set()
        max_money = self.limits.max_money
        if not is_coinbase:
            for idx, txin in enumerate(tx.vin):
                outpoint = (txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if outpoint in seen_inputs:
                    logger.error("Duplicate input in transaction")
                    return False
                seen_inputs.add(outpoint)
                utxo = self.utxo_store.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if not utxo:
                    logger.error(f"UTXO not found: {txin.prev_tx_hash.hex()}:{txin.prev_tx_index}")
                    return False
                if utxo['is_coinbase'] and height - utxo['height'] < self.coinbase_maturity:
                    logger.error("Coinbase UTXO not mature")
                    return False
                script_pubkey = utxo.get("script_pubkey", b"")
                if not isinstance(script_pubkey, (bytes, bytearray)):
                    script_pubkey = bytes(script_pubkey)
                if not verify_input_script(
                    tx,
                    idx,
                    txin.script_sig,
                    bytes(script_pubkey),
                    int(utxo.get("value", 0)),
                ):
                    logger.error(
                        "Script/signature verification failed for input %s:%s",
                        txin.prev_tx_hash.hex(),
                        txin.prev_tx_index,
                    )
                    return False
        total_out = 0
        for txout in tx.vout:
            if txout.value < 0:
                logger.error(f"Negative output value: {txout.value}")
                return False
            if txout.value > max_money:
                logger.error("Output value exceeds max money")
                return False
            total_out += txout.value
            if self.limits.is_dust_output(txout.value, txout.script_pubkey):
                logger.warning(f"Dust output: {txout.value} satoshis")
        if total_out > max_money:
            logger.error("Total output exceeds max supply")
            return False
        if not is_coinbase:
            total_in = 0
            for txin in tx.vin:
                utxo = self.utxo_store.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if utxo:
                    total_in += int(utxo['value'])
            if total_in < total_out:
                logger.error("Transaction spends more than inputs")
                return False
        return True

    def validate_coinbase(self, coinbase: Transaction, height: int) -> bool:
        if len(coinbase.vin) != 1:
            logger.error(f"Coinbase has {len(coinbase.vin)} inputs")
            return False
        script_len = len(coinbase.vin[0].script_sig)
        if not self.limits.is_coinbase_script_length_valid(script_len):
            logger.error(f"Invalid coinbase script length: {script_len}")
            return False
        if height >= self.params.bip34_height:
            height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
            script = coinbase.vin[0].script_sig
            if len(script) < len(height_bytes):
                logger.error("Coinbase script too short for height")
                return False
            strict_bip34 = is_consensus_feature_active(
                self.params, SOFTFORK_BIP34_STRICT, height
            )
            if strict_bip34:
                if len(script) < 1 + len(height_bytes):
                    logger.error("Coinbase script too short for strict BIP34")
                    return False
                if script[0] != len(height_bytes):
                    logger.error("Coinbase height push length mismatch")
                    return False
                if script[1:1 + len(height_bytes)] != height_bytes:
                    logger.error("Coinbase height not minimally encoded at script start")
                    return False
            elif script[0] != len(height_bytes):
                logger.warning("Coinbase height not properly encoded")
        return True

    def validate_subsidy(self, block: Block, height: int) -> bool:
        from shared.consensus.subsidy import get_block_subsidy
        expected_subsidy = get_block_subsidy(height, self.params)
        coinbase = block.transactions[0]
        total_out = sum(txout.value for txout in coinbase.vout)
        total_fees = self._calculate_fees(block)
        if total_fees < 0:
            logger.error("Invalid negative fee total while validating subsidy")
            return False
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
        if block.header.timestamp > time.time() + self.limits.max_future_block_time_seconds:
            logger.error(f"Block timestamp too far in future: {block.header.timestamp}")
            return False
        return True

    def _calculate_fees(self, block: Block) -> int:
        total_fees = 0
        for tx in block.transactions[1:]:
            tx_input = 0
            tx_output = 0
            for txin in tx.vin:
                utxo = self.utxo_store.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
                if not utxo:
                    return -1
                tx_input += int(utxo.get('value', 0))
            for txout in tx.vout:
                tx_output += int(txout.value)
            fee = tx_input - tx_output
            if fee < 0:
                return -1
            total_fees += fee
        return total_fees

    def _get_median_time_past(self, height: int) -> int:
        times = []
        window = max(1, self.limits.median_time_past_window)
        for h in range(max(0, height - window), height):
            entry = self.block_index.get_block_by_height(h)
            if entry:
                times.append(entry.header.timestamp)
        if not times:
            return 0
        times.sort()
        return times[len(times) // 2]
