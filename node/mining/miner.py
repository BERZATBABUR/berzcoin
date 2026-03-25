"""CPU miner for BerzCoin."""

import asyncio
import time
from typing import Optional, Dict, Any, Callable, Awaitable

from shared.core.block import Block, BlockHeader
from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger
from .block_assembler import BlockAssembler
from node.chain.chainstate import ChainState

logger = get_logger()


class CPUMiner:
    """CPU-based miner for testing and regtest."""

    def __init__(
        self,
        chainstate: ChainState,
        block_assembler: BlockAssembler,
        mining_address: Optional[str] = None,
    ):
        self.chainstate = chainstate
        self.block_assembler = block_assembler
        self.mining_address = mining_address
        self.pow = ProofOfWork(chainstate.params)

        self.is_mining = False
        self.mining_task: Optional[asyncio.Task] = None
        self.blocks_mined = 0
        self.total_hashes = 0
        self.start_time = 0.0

        self.on_block_mined: Optional[Callable[[Block], Awaitable[None]]] = None

    async def start_mining(self, address: Optional[str] = None, threads: int = 1) -> None:
        if self.is_mining:
            logger.warning("Miner already running")
            return

        self.is_mining = True
        self.start_time = time.time()
        self.mining_address = address or self.mining_address

        if not self.mining_address:
            raise ValueError("No mining address provided")

        logger.info(f"Starting CPU miner with {threads} task(s) to {self.mining_address}")

        self.mining_task = asyncio.create_task(self._mine_loop(threads))

    async def stop_mining(self) -> None:
        if not self.is_mining:
            return

        self.is_mining = False

        if self.mining_task:
            self.mining_task.cancel()
            try:
                await self.mining_task
            except asyncio.CancelledError:
                pass

        elapsed = time.time() - self.start_time
        avg_hashrate = self.total_hashes / elapsed if elapsed > 0 else 0

        logger.info(
            f"Miner stopped. Mined {self.blocks_mined} blocks, "
            f"Avg hashrate: {avg_hashrate:.2f} H/s"
        )

    async def _mine_loop(self, threads: int) -> None:
        while self.is_mining:
            try:
                template = await self.block_assembler.create_block_template(self.mining_address)
                block = self._create_block_from_template(template)
                mined_block = await self._mine_block(block, threads)

                if mined_block:
                    success = await self._submit_block(mined_block)

                    if success:
                        self.blocks_mined += 1
                        logger.info(
                            f"Mined block at height {self.chainstate.get_best_height() + 1}: "
                            f"{mined_block.header.hash_hex()[:16]}"
                        )

                        if self.on_block_mined:
                            await self.on_block_mined(mined_block)

                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Mining error: {e}")
                await asyncio.sleep(1)

    async def _mine_block(self, block: Block, threads: int) -> Optional[Block]:
        target = self.pow.get_target(block.header.bits)
        header = block.header

        nonce_range = 0xffffffff // max(1, threads)
        tasks = []

        for i in range(threads):
            start_nonce = i * nonce_range
            end_nonce = (start_nonce + nonce_range - 1) if i < threads - 1 else 0xffffffff

            tasks.append(
                asyncio.create_task(
                    self._mine_range(header, start_nonce, end_nonce, target)
                )
            )

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        for task in done:
            try:
                nonce = task.result()
                if nonce is not None:
                    header.nonce = nonce
                    return block
            except asyncio.CancelledError:
                pass

        return None

    async def _mine_range(
        self,
        header: BlockHeader,
        start_nonce: int,
        end_nonce: int,
        target: int,
    ) -> Optional[int]:
        """Scan nonces; yield to the event loop periodically."""
        chunk = 2048
        nonce = start_nonce

        while nonce <= end_nonce and self.is_mining:
            limit = min(nonce + chunk - 1, end_nonce)
            while nonce <= limit:
                self.total_hashes += 1
                header.nonce = nonce
                if header.is_valid_pow(target):
                    return nonce
                nonce += 1

            await asyncio.sleep(0)

        return None

    def _create_block_from_template(self, template: Dict[str, Any]) -> Block:
        from shared.core.transaction import Transaction

        header_bytes = bytes.fromhex(template['header'])
        header, _ = BlockHeader.deserialize(header_bytes)

        coinbase_tx, _ = Transaction.deserialize(bytes.fromhex(template['coinbase_tx']))
        transactions = [coinbase_tx]

        for tx_data in template['transactions']:
            tx, _ = Transaction.deserialize(bytes.fromhex(tx_data['data']))
            transactions.append(tx)

        return Block(header, transactions)

    async def _submit_block(self, block: Block) -> bool:
        """Hook for accepting a mined block (wire to node / RPC)."""
        _ = block
        return True

    def get_stats(self) -> Dict[str, Any]:
        elapsed = time.time() - self.start_time if self.start_time > 0 else 0
        avg_hashrate = self.total_hashes / elapsed if elapsed > 0 else 0

        return {
            'mining': self.is_mining,
            'blocks_mined': self.blocks_mined,
            'total_hashes': self.total_hashes,
            'uptime': elapsed,
            'avg_hashrate': avg_hashrate,
            'current_hashrate': 0,
            'mining_address': self.mining_address,
        }

    async def mine_single_block(self, address: Optional[str] = None) -> Optional[Block]:
        """Mine one block (e.g. regtest `generate`); enables PoW scan even if loop miner is off."""
        was_mining = self.is_mining
        self.is_mining = True
        try:
            template = await self.block_assembler.create_block_template(address or self.mining_address)
            block = self._create_block_from_template(template)
            mined_block = await self._mine_block(block, 1)

            if mined_block:
                await self._submit_block(mined_block)
                return mined_block

            return None
        finally:
            self.is_mining = was_mining
