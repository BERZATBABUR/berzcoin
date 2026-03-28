"""Mining node with continuous proof-of-work search."""

import asyncio
import time
from typing import Optional, List, Dict, Any, Callable, Awaitable

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
    """Mining node that continuously searches for valid PoW blocks."""

    def __init__(self, chainstate: ChainState, mempool: Mempool,
                 mining_address: str, p2p_manager=None,
                 address_guard: Optional[Callable[[str], bool]] = None,
                 block_acceptor: Optional[
                     Callable[[Block, Optional[str], bool], Awaitable[tuple[bool, str, Optional[str]]]]
                 ] = None):
        self.chainstate = chainstate
        self.mempool = mempool
        self.mining_address = mining_address
        self.p2p_manager = p2p_manager
        self.address_guard = address_guard
        self.block_acceptor = block_acceptor

        self.is_mining = False
        self.mining_task: Optional[asyncio.Task] = None
        self.blocks_mined = 0
        self.total_hashes = 0
        self.start_time = 0
        self.last_reward_address = ""
        self.last_subsidy_sats = 0
        self.last_fees_sats = 0
        self.last_reward_sats = 0
        self.last_stop_reason = ""

        self.pow = ProofOfWork(chainstate.params)
        self.target_time = int(chainstate.params.pow_target_spacing)

    async def start_mining(self, mining_address: Optional[str] = None, threads: int = 1) -> None:
        """Start mining loop."""
        if mining_address:
            self.mining_address = mining_address

        if not self.mining_address:
            logger.error("No mining address set")
            return
        if not self._guard_allows_current_address():
            self.last_stop_reason = "mining_address_wallet_mismatch"
            logger.error("Mining start denied: mining address does not match active wallet address")
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
                # Keep the event loop responsive while mining in-process with RPC/dashboard.
                await asyncio.sleep(0)
                if not self._guard_allows_current_address():
                    self.last_stop_reason = "mining_address_wallet_mismatch"
                    logger.warning("Stopping mining: mining address no longer matches active wallet")
                    self.is_mining = False
                    break

                cycle_started_at = time.time()
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

                # Get current difficulty
                bits = self._get_next_bits(best_header, next_height)
                # Continuous nonce/extra-nonce search on this candidate tip.
                extra_nonce = 0
                while self.is_mining:
                    if not self._guard_allows_current_address():
                        self.last_stop_reason = "mining_address_wallet_mismatch"
                        self.is_mining = False
                        break

                    # If chain tip changes, rebuild candidate on latest tip.
                    if self.chainstate.get_best_block_hash() != best_hash:
                        break

                    coinbase_tx = self._create_coinbase(
                        next_height, coinbase_value, extra_nonce=extra_nonce
                    )
                    all_txs = [coinbase_tx] + transactions
                    tx_hashes = [tx.txid() for tx in all_txs]
                    merkle_root_hash = merkle_root(tx_hashes)
                    if merkle_root_hash is None:
                        merkle_root_hash = b'\x00' * 32

                    # Header timestamp must be > MTP.
                    timestamp = max(int(time.time()), self._get_median_time() + 1)
                    header = BlockHeader(
                        version=1,
                        prev_block_hash=bytes.fromhex(best_hash),
                        merkle_root=merkle_root_hash,
                        timestamp=timestamp,
                        bits=bits,
                        nonce=0,
                    )

                    mined_header = await self._mine_block(header, max_nonce=(2**32))
                    if mined_header:
                        block = Block(mined_header, all_txs)
                        connected = await self._submit_block(block)
                        if not connected:
                            await asyncio.sleep(0.1)
                            break

                        if self.p2p_manager:
                            await self.p2p_manager.broadcast_block(block)

                        self.blocks_mined += 1
                        self.last_reward_address = self.mining_address
                        self.last_subsidy_sats = subsidy
                        self.last_fees_sats = total_fees
                        self.last_reward_sats = coinbase_value
                        logger.info(
                            "Block mined! Height: %s, Time: %.2fs, Hash: %s...",
                            next_height,
                            time.time() - self.start_time,
                            block.header.hash_hex()[:16],
                        )
                        await self._pace_to_target_spacing(cycle_started_at)
                        break

                    extra_nonce += 1
                    await asyncio.sleep(0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Mining error: {e}")
                await asyncio.sleep(1)

    async def _mine_block(
        self,
        header: BlockHeader,
        allow_when_stopped: bool = False,
        max_nonce: int = 2**32,
    ) -> Optional[BlockHeader]:
        """Mine a block by finding a valid nonce."""
        target = self.pow.get_target(header.bits)
        start_time = time.time()
        nonce = 0

        while nonce < max_nonce and (self.is_mining or allow_when_stopped):
            self.total_hashes += 1
            header.nonce = nonce

            if header.is_valid_pow(target):
                mining_time = time.time() - start_time
                logger.info(f"Block found! Nonce: {nonce}, Time: {mining_time:.2f}s")
                # Make nonce-0 and low-difficulty wins cooperative with other tasks.
                await asyncio.sleep(0)
                return header

            nonce += 1
            if nonce % 50_000 == 0:
                await asyncio.sleep(0)
        return None

    async def _submit_block(self, block: Block) -> bool:
        """Submit mined block to chain."""
        if self.block_acceptor is not None:
            accepted, _block_hash, reason = await self.block_acceptor(
                block, None, False
            )
            if not accepted and reason in {"stateful_validation_failed", "connect_failed"}:
                await self._drop_candidate_transactions(block, reason)
            return bool(accepted)

        from node.validation.connect import ConnectBlock
        from node.wallet.core.tx_builder import TransactionBuilder

        height = self.chainstate.get_best_height() + 1

        if not block.transactions:
            return False
        expected_script = TransactionBuilder(self.chainstate.network)._create_script_pubkey(self.mining_address)
        if not block.transactions[0].vout or block.transactions[0].vout[0].script_pubkey != expected_script:
            logger.error("Coinbase reward address mismatch; rejecting mined block")
            return False

        if not self.chainstate.validate_block_stateful(block, height):
            logger.error(f"Invalid block at height {height}")
            await self._drop_candidate_transactions(block, "stateful_validation_failed")
            return False

        block_hash = block.header.hash_hex()
        if self.chainstate.block_index.get_block(block_hash):
            return True

        block_work = self.chainstate.chainwork.calculate_chain_work([block.header])
        chainwork_total = self.chainstate.get_best_chainwork() + block_work
        self.chainstate.blocks_store.write_block(block, height)
        self.chainstate.block_index.add_block(block, height, chainwork_total)

        connect = ConnectBlock(
            self.chainstate.utxo_store,
            self.chainstate.block_index,
            network=self.chainstate.params.get_network_name(),
        )
        if not connect.connect(block):
            return False

        self.chainstate.set_best_block(block_hash, height, chainwork_total)
        self.chainstate.header_chain.add_header(block.header, height, chainwork_total)

        # Remove confirmed transactions and revalidate remaining mempool entries.
        await self.mempool.handle_connected_block(block)

        logger.info(f"Block {height} added to chain")
        return True

    async def _drop_candidate_transactions(self, block: Block, reason: str) -> None:
        """Avoid repeated mining failures by evicting invalid candidate txs from mempool."""
        if not self.mempool or len(block.transactions) <= 1:
            return
        removed_total = 0
        for tx in block.transactions[1:]:
            removed = await self.mempool.remove_transaction(tx.txid().hex(), include_descendants=True)
            removed_total += len(removed)
        if removed_total > 0:
            logger.warning(
                "Dropped %s mempool tx(s) after block rejection (%s)",
                removed_total,
                reason,
            )

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

    def _create_coinbase(self, height: int, value: int, extra_nonce: int = 0) -> Transaction:
        """Create coinbase transaction."""
        from node.wallet.core.tx_builder import TransactionBuilder

        builder = TransactionBuilder(self.chainstate.network)

        height_bytes = height.to_bytes((height.bit_length() + 7) // 8, 'little')
        extra = extra_nonce.to_bytes(max(1, (extra_nonce.bit_length() + 7) // 8), "little")
        coinbase_script = (
            bytes([len(height_bytes)]) + height_bytes + bytes([len(extra)]) + extra + b"/BerzCoin/"
        )

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
        """Get next difficulty bits from consensus retarget rules."""
        if height == 0:
            return self.chainstate.params.genesis_bits
        if self.chainstate.params.pow_no_retargeting:
            return best_header.bits

        interval = max(1, int(self.chainstate.params.retarget_interval_blocks()))
        if height % interval != 0:
            return best_header.bits

        start_height = height - interval
        if start_height < 0:
            return best_header.bits

        headers = []
        for h in range(start_height, height):
            hdr = self.chainstate.get_header(h)
            if hdr is None:
                return best_header.bits
            headers.append(hdr)

        return self.pow.get_next_work_required(headers, height - 1)

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

        coinbase_tx = self._create_coinbase(next_height, coinbase_value, extra_nonce=0)

        all_txs = [coinbase_tx] + transactions
        tx_hashes = [tx.txid() for tx in all_txs]
        merkle_root_hash = merkle_root(tx_hashes)

        timestamp = max(int(time.time()), self._get_median_time() + 1)

        header = BlockHeader(
            version=1,
            prev_block_hash=bytes.fromhex(best_hash),
            merkle_root=merkle_root_hash,
            timestamp=timestamp,
            bits=self._get_next_bits(best_header, next_height),
            nonce=0
        )

        mined_header = await self._mine_block(header, allow_when_stopped=True)

        if mined_header:
            return Block(mined_header, all_txs)

        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get mining statistics."""
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        hashrate = self.total_hashes / elapsed if elapsed > 0 else 0

        return {
            'mining': self.is_mining,
            'avg_hashrate': hashrate,
            'mining_active': self.is_mining,
            'blocks_mined': self.blocks_mined,
            'total_hashes': self.total_hashes,
            'hashrate': hashrate,
            'mining_address': self.mining_address,
            'target_block_time': self.target_time,
            'last_reward_address': self.last_reward_address,
            'last_subsidy_sats': self.last_subsidy_sats,
            'last_fees_sats': self.last_fees_sats,
            'last_reward_sats': self.last_reward_sats,
            'last_stop_reason': self.last_stop_reason,
            'uptime': elapsed
        }

    def _guard_allows_current_address(self) -> bool:
        if not self.address_guard:
            return True
        try:
            return bool(self.address_guard(self.mining_address))
        except Exception:
            return False

    async def _pace_to_target_spacing(self, cycle_started_at: float) -> None:
        """Throttle fast regtest loops so mined blocks follow configured target spacing."""
        if self.target_time <= 0:
            await asyncio.sleep(0)
            return
        remaining = self.target_time - (time.time() - cycle_started_at)
        if remaining <= 0:
            await asyncio.sleep(0)
            return
        logger.debug("Pacing miner for %.2fs to match target block time", remaining)
        await asyncio.sleep(remaining)
