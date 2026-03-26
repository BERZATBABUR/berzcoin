"""Mining node with 2-minute block time."""

import asyncio
import time
from typing import Optional, List, Dict, Any

from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction
from shared.core.merkle import merkle_root
from shared.consensus.pow import ProofOfWork
from shared.consensus.subsidy import get_block_subsidy
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool

logger = get_logger()


class MiningNode:
    """Mining node with 2-minute target block time."""

    TARGET_BLOCK_TIME = 120  # 2 minutes

    def __init__(self, chainstate: ChainState, mempool: Mempool,
                 mining_address: str, p2p_manager=None):
        self.chainstate = chainstate
        self.mempool = mempool
        self.mining_address = mining_address
        self.p2p_manager = p2p_manager

        self.is_mining = False
        self.mining_task: Optional[asyncio.Task] = None
        self.blocks_mined = 0
        self.total_hashes = 0
        self.start_time = 0

        self.pow = ProofOfWork(chainstate.params)
        self.target_time = self.TARGET_BLOCK_TIME

    async def start_mining(self, mining_address: Optional[str] = None, threads: int = 1) -> None:
        """Start mining loop."""
        if mining_address:
            self.mining_address = mining_address

        if not self.mining_address:
            logger.error("No mining address set")
            return

        if self.is_mining:
            return

        self.is_mining = True
        self.start_time = time.time()
        self.mining_task = asyncio.create_task(self._mine_loop())
        logger.info(f"Mining started with address: {self.mining_address}, target time: {self.target_time}s")

    async def stop_mining(self) -> None:
        """Stop mining."""
        self.is_mining = False
        if self.mining_task:
            self.mining_task.cancel()
            try:
                await self.mining_task
            except asyncio.CancelledError:
                pass
        logger.info("Mining stopped")

    async def _mine_loop(self) -> None:
        """Main mining loop."""
        while self.is_mining:
            try:
                best_height = self.chainstate.get_best_height()
                best_hash = self.chainstate.get_best_block_hash()
                best_header = self.chainstate.get_header(best_height)

                if not best_header:
                    await asyncio.sleep(1)
                    continue

                next_height = best_height + 1

                # Select transactions from mempool
                transactions = await self._select_transactions()

                # Calculate block reward
                subsidy = get_block_subsidy(next_height, self.chainstate.params)
                total_fees = await self._calculate_fees(transactions)
                coinbase_value = subsidy + total_fees

                # Create coinbase transaction
                coinbase_tx = self._create_coinbase(next_height, coinbase_value)

                # Build full transaction list
                all_txs = [coinbase_tx] + transactions

                # Calculate merkle root
                tx_hashes = [tx.txid() for tx in all_txs]
                merkle_root_hash = merkle_root(tx_hashes)
                if merkle_root_hash is None:
                    merkle_root_hash = b'\x00' * 32

                # Get current difficulty
                bits = self._get_next_bits(best_header, next_height)

                # Calculate timestamp (must be > median time)
                timestamp = max(int(time.time()), self._get_median_time() + 1)

                # Create block header
                header = BlockHeader(
                    version=1,
                    prev_block_hash=bytes.fromhex(best_hash),
                    merkle_root=merkle_root_hash,
                    timestamp=timestamp,
                    bits=bits,
                    nonce=0
                )

                # Mine block
                mined_header = await self._mine_block(header)

                if mined_header:
                    # Create block
                    block = Block(mined_header, all_txs)

                    # Submit and broadcast
                    await self._submit_block(block)

                    if self.p2p_manager:
                        await self.p2p_manager.broadcast_block(block)

                    self.blocks_mined += 1
                    logger.info(f"Block mined! Height: {next_height}, Time: {time.time() - self.start_time:.2f}s, Hash: {block.header.hash_hex()[:16]}...")

                # Small delay between mining attempts
                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Mining error: {e}")
                await asyncio.sleep(1)

    async def _mine_block(self, header: BlockHeader) -> Optional[BlockHeader]:
        """Mine a block by finding a valid nonce."""
        target = self.pow.get_target(header.bits)
        start_time = time.time()
        nonce = 0

        while self.is_mining:
            self.total_hashes += 1
            header.nonce = nonce

            if header.is_valid_pow(target):
                mining_time = time.time() - start_time
                logger.info(f"Block found! Nonce: {nonce}, Time: {mining_time:.2f}s")
                return header

            nonce += 1

            # Timeout after target time
            if time.time() - start_time > self.target_time:
                logger.debug(f"Mining timeout after {self.target_time}s")
                return None

        return None

    async def _submit_block(self, block: Block) -> bool:
        """Submit mined block to chain."""
        from node.validation.connect import ConnectBlock

        height = self.chainstate.get_best_height() + 1

        if not self.chainstate.rules.validate_block(block, height):
            logger.error(f"Invalid block at height {height}")
            return False

        connect = ConnectBlock(self.chainstate.utxo_store, self.chainstate.block_index)

        if connect.connect(block):
            block_hash = block.header.hash_hex()
            chainwork = self.chainstate.chainwork.calculate_chain_work([block.header])
            self.chainstate.set_best_block(block_hash, height, chainwork)

            # Remove confirmed transactions from mempool
            for tx in block.transactions[1:]:
                await self.mempool.remove_transaction(tx.txid().hex())

            logger.info(f"Block {height} added to chain")
            return True

        return False

    async def _select_transactions(self) -> List[Transaction]:
        """Select transactions from mempool (highest fee first)."""
        if not self.mempool:
            return []

        all_txs = await self.mempool.get_transactions()

        txs_with_fees = []
        for tx in all_txs:
            fee = await self._get_transaction_fee(tx)
            size = len(tx.serialize())
            if size > 0:
                txs_with_fees.append((tx, fee / size))

        txs_with_fees.sort(key=lambda x: x[1], reverse=True)

        selected = []
        current_weight = 4000

        for tx, _ in txs_with_fees:
            tx_weight = tx.weight()
            if current_weight + tx_weight <= self.chainstate.params.max_block_weight:
                selected.append(tx)
                current_weight += tx_weight

        return selected

    async def _calculate_fees(self, transactions: List[Transaction]) -> int:
        """Calculate total fees."""
        total = 0
        for tx in transactions:
            total += await self._get_transaction_fee(tx)
        return total

    async def _get_transaction_fee(self, tx: Transaction) -> int:
        """Calculate transaction fee."""
        total_input = 0
        total_output = sum(txout.value for txout in tx.vout)

        for txin in tx.vin:
            utxo = self.chainstate.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
            if utxo:
                total_input += utxo.get('value', 0)

        return total_input - total_output

    def _create_coinbase(self, height: int, value: int) -> Transaction:
        """Create coinbase transaction."""
        from node.wallet.core.tx_builder import TransactionBuilder

        builder = TransactionBuilder(self.chainstate.network)

        height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
        coinbase_script = bytes([len(height_bytes)]) + height_bytes + b"/BerzCoin/"

        from shared.core.transaction import TxIn, TxOut, Transaction

        txin = TxIn(
            prev_tx_hash=b'\x00' * 32,
            prev_tx_index=0xffffffff,
            script_sig=coinbase_script,
            sequence=0xffffffff
        )

        script_pubkey = builder._create_script_pubkey(self.mining_address)
        txout = TxOut(value=value, script_pubkey=script_pubkey)

        tx = Transaction(version=1)
        tx.vin.append(txin)
        tx.vout.append(txout)

        return tx

    def _get_next_bits(self, best_header: BlockHeader, height: int) -> int:
        """Get next difficulty bits with 2-minute target."""
        if height == 0:
            return self.chainstate.params.genesis_bits

        # Simple difficulty adjustment based on actual block time
        # In production, use proper retargeting
        return best_header.bits

    def _get_median_time(self) -> int:
        """Get median time of last 11 blocks."""
        best_height = self.chainstate.get_best_height()

        times = []
        for h in range(max(0, best_height - 10), best_height + 1):
            block = self.chainstate.get_block_by_height(h)
            if block:
                times.append(block.header.timestamp)

        if not times:
            return 0

        times.sort()
        return times[len(times) // 2]

    async def mine_single_block(self, address: str = None) -> Optional[Block]:
        """Mine a single block (for regtest)."""
        if address:
            self.mining_address = address

        best_height = self.chainstate.get_best_height()
        best_hash = self.chainstate.get_best_block_hash()
        best_header = self.chainstate.get_header(best_height)

        if not best_header:
            return None

        next_height = best_height + 1

        transactions = await self._select_transactions()

        subsidy = get_block_subsidy(next_height, self.chainstate.params)
        total_fees = await self._calculate_fees(transactions)
        coinbase_value = subsidy + total_fees

        coinbase_tx = self._create_coinbase(next_height, coinbase_value)

        all_txs = [coinbase_tx] + transactions
        tx_hashes = [tx.txid() for tx in all_txs]
        merkle_root_hash = merkle_root(tx_hashes)

        timestamp = max(int(time.time()), self._get_median_time() + 1)

        header = BlockHeader(
            version=1,
            prev_block_hash=bytes.fromhex(best_hash),
            merkle_root=merkle_root_hash,
            timestamp=timestamp,
            bits=best_header.bits,
            nonce=0
        )

        mined_header = await self._mine_block(header)

        if mined_header:
            return Block(mined_header, all_txs)

        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get mining statistics."""
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        hashrate = self.total_hashes / elapsed if elapsed > 0 else 0

        return {
            'mining_active': self.is_mining,
            'blocks_mined': self.blocks_mined,
            'total_hashes': self.total_hashes,
            'hashrate': hashrate,
            'mining_address': self.mining_address,
            'target_block_time': self.target_time,
            'uptime': elapsed
        }
