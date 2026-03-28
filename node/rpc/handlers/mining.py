"""Mining RPC handlers."""

import time
from typing import Any, Dict, List, Optional

from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction
from shared.core.merkle import merkle_root
from shared.consensus.subsidy import get_block_subsidy
from shared.consensus.pow import ProofOfWork
from shared.consensus.weights import calculate_transaction_weight
from shared.protocol.messages import InvMessage
from shared.utils.logging import get_logger

logger = get_logger()


class MiningHandlers:
    """RPC handlers for mining operations."""

    def __init__(self, node: Any):
        self.node = node

    async def get_mining_info(self) -> Dict[str, Any]:
        chain = self.node.chainstate
        best_height = chain.get_best_height()
        best_header = chain.get_header(best_height) if best_height >= 0 else None

        if not best_header:
            return {'error': 'No blocks yet'}

        return {
            'blocks': best_height,
            'current_block_size': 0,
            'current_block_weight': 0,
            'current_block_tx': 0,
            'difficulty': self._get_difficulty(best_header.bits),
            'errors': '',
            'network_hashps': self._get_network_hashrate(),
            'pooledtx': len(self.node.mempool.transactions) if self.node.mempool else 0,
            'chain': getattr(self.node, 'network', 'mainnet'),
            'warnings': ''
        }

    async def get_block_template(self, template_request: Optional[Dict] = None) -> Dict[str, Any]:
        _ = template_request
        chain = self.node.chainstate
        mempool = self.node.mempool

        if not chain or not mempool:
            return {'error': 'Chain or mempool not ready'}

        best_height = chain.get_best_height()
        best_hash = chain.get_best_block_hash()
        best_header = chain.get_header(best_height) if best_height >= 0 else None

        if not best_hash or not best_header:
            return {'error': 'No chain tip'}

        height = best_height + 1

        subsidy = get_block_subsidy(height, chain.params)

        max_weight = chain.params.max_block_weight
        transactions = await mempool.get_transactions_for_block(max_weight - 4000)

        total_fees = 0
        for tx in transactions:
            tid = tx.txid().hex()
            ent = mempool.transactions.get(tid)
            if ent:
                total_fees += ent.fee

        from node.wallet.core.tx_builder import TransactionBuilder
        tx_builder = TransactionBuilder(getattr(self.node, 'network', 'mainnet'))

        mining_address = getattr(self.node, 'mining_address', None) or "1111111111111111111114oLvT2"
        coinbase_tx = tx_builder.create_coinbase(height, subsidy + total_fees, mining_address)

        all_txs = [coinbase_tx] + transactions
        tx_hashes = [tx.txid() for tx in all_txs]
        mr = merkle_root(tx_hashes)
        if mr is None:
            mr = b'\x00' * 32

        med_time = self._median_time_past(chain, best_height)
        ts = int(med_time) + 1

        bits = self._next_bits(chain, best_height, best_header)

        header = BlockHeader(
            version=0x20000000,
            prev_block_hash=best_header.hash(),
            merkle_root=mr,
            timestamp=ts,
            bits=bits,
            nonce=0
        )

        return {
            'version': header.version,
            'previousblockhash': best_hash,
            'merkleroot': mr.hex(),
            'transactions': [{
                'data': tx.serialize().hex(),
                'txid': tx.txid().hex(),
                'hash': tx.txid().hex(),
                'fee': mempool.transactions[tx.txid().hex()].fee if tx.txid().hex() in mempool.transactions else 0,
                'weight': calculate_transaction_weight(tx)
            } for tx in transactions],
            'coinbasetxn': {'data': coinbase_tx.serialize().hex()},
            'coinbaseaux': {},
            'coinbasevalue': subsidy + total_fees,
            'longpollid': f"{best_height}:{best_hash}",
            'target': hex(header.bits),
            'mintime': header.timestamp,
            'mutable': ['time', 'transactions', 'prevblock'],
            'noncerange': '00000000ffffffff',
            'sigoplimit': 80000,
            'sizelimit': 1000000,
            'weightlimit': chain.params.max_block_weight,
            'curtime': header.timestamp,
            'bits': hex(header.bits),
            'height': height
        }

    async def submit_block(self, hex_data: str) -> str:
        """Validate and connect a candidate block to the active chain tip."""
        block_bytes = bytes.fromhex(hex_data.strip())
        block, _ = Block.deserialize(block_bytes)
        block_hash = block.header.hash_hex()

        if hasattr(self.node, "on_block"):
            accepted, _bh, reason = await self.node.on_block(
                block, source_peer=None, relay=False
            )
            if accepted or reason == "known":
                return block_hash
            raise ValueError(f"Block rejected: {reason}")

        chain = self.node.chainstate
        height = chain.get_best_height() + 1
        if not chain.validate_block_stateful(block, height):
            raise ValueError(f"Invalid block at height {height}")
        if chain.block_index.get_block(block_hash):
            return block_hash

        block_work = chain.chainwork.calculate_chain_work([block.header])
        chainwork_total = chain.get_best_chainwork() + block_work

        chain.blocks_store.write_block(block, height)
        chain.block_index.add_block(block, height, chainwork_total)

        connect = ConnectBlock(
            chain.utxo_store,
            chain.block_index,
            network=chain.params.get_network_name(),
        )
        if not connect.connect(block):
            raise ValueError("Block connect failed")

        chain.set_best_block(block_hash, height, chainwork_total)
        chain.header_chain.add_header(block.header, height, chainwork_total)
        logger.info("submit_block: connected block %d %s", height, block_hash[:16])
        return block_hash

    async def get_network_hashps(self, blocks: int = 120, height: int = -1) -> float:
        _ = blocks, height
        return self._get_network_hashrate()

    async def get_difficulty(self) -> float:
        chain = self.node.chainstate
        best_h = chain.get_best_height()
        best_header = chain.get_header(best_h) if best_h >= 0 else None

        if not best_header:
            return 1.0

        return self._get_difficulty(best_header.bits)

    async def generate(self, num_blocks: int, address: Optional[str] = None,
                       maxtries: int = 1000000) -> List[str]:
        """Generate blocks (regtest only). Uses the CPU miner and connects blocks to the chain."""
        _ = maxtries
        if self.node.config.get("network") != "regtest":
            raise ValueError("This command is only available on regtest")
        if not self.node.miner:
            raise ValueError("Miner not initialized")

        from node.validation.connect import ConnectBlock

        chain = self.node.chainstate
        generated: List[str] = []

        for _ in range(num_blocks):
            block = await self.node.miner.mine_single_block(address)
            if not block:
                logger.warning("Failed to mine block")
                break

            if hasattr(self.node, "on_block"):
                accepted, block_hash, reason = await self.node.on_block(
                    block, source_peer=None, relay=True
                )
                if not accepted:
                    logger.warning("generate: invalid block: %s", reason)
                    break
                generated.append(block_hash)
                logger.info("Generated block %s: %s", chain.get_best_height(), block_hash[:16])
                continue

            height = chain.get_best_height() + 1
            if not chain.validate_block_stateful(block, height):
                logger.warning("generate: invalid block at height %d", height)
                break

            block_work = chain.chainwork.calculate_chain_work([block.header])
            chainwork_total = chain.get_best_chainwork() + block_work

            chain.blocks_store.write_block(block, height)
            chain.block_index.add_block(block, height, chainwork_total)

            connect = ConnectBlock(
                chain.utxo_store,
                chain.block_index,
                network=chain.params.get_network_name(),
            )
            if not connect.connect(block):
                logger.warning("Failed to connect block")
                break

            block_hash = block.header.hash_hex()
            chain.set_best_block(block_hash, height, chainwork_total)
            chain.header_chain.add_header(block.header, height, chainwork_total)

            generated.append(block_hash)
            logger.info(f"Generated block {height}: {block_hash[:16]}")

        return generated

    def _get_difficulty(self, bits: int) -> float:
        pow_check = ProofOfWork(self.node.chainstate.params)
        return pow_check.calculate_difficulty(bits)

    def _get_network_hashrate(self) -> float:
        chain = self.node.chainstate
        best_height = chain.get_best_height()

        if best_height < 120:
            return 0.0

        start_height = best_height - 119
        start_time: Optional[int] = None
        end_time: Optional[int] = None

        for h in range(start_height, best_height + 1):
            block = chain.get_block_by_height(h)
            if block:
                if start_time is None:
                    start_time = block.header.timestamp
                end_time = block.header.timestamp

        if start_time is None or end_time is None:
            return 0.0

        span = end_time - start_time
        if span <= 0:
            return 0.0

        total_work = 0
        for h in range(start_height, best_height + 1):
            header = chain.get_header(h)
            if header:
                total_work += self.node.chainstate.chainwork.calculate_block_work(header.bits)

        return total_work / span

    def _median_time_past(self, chain: Any, best_height: int) -> int:
        times: List[int] = []
        for h in range(max(0, best_height - 10), best_height + 1):
            b = chain.get_block_by_height(h)
            if b:
                times.append(b.header.timestamp)
        if not times:
            return int(time.time())
        times.sort()
        return times[len(times) // 2]

    def _next_bits(self, chain: Any, best_height: int, tip_header: BlockHeader) -> int:
        headers: List[BlockHeader] = []
        interval = chain.params.retarget_interval_blocks()
        start = max(0, best_height - (interval - 1))
        for h in range(start, best_height + 1):
            hh = chain.get_header(h)
            if hh:
                headers.append(hh)
        if len(headers) < interval:
            return tip_header.bits

        pow_check = ProofOfWork(chain.params)
        return pow_check.get_next_work_required(headers, best_height)

    def _create_block_from_template(self, template: Dict[str, Any]) -> Optional[Block]:
        try:
            cb_data = template.get('coinbasetxn', {}).get('data')
            if not cb_data:
                return None
            coinbase, _ = Transaction.deserialize(bytes.fromhex(cb_data))

            txs: List[Transaction] = [coinbase]
            for ent in template.get('transactions', []):
                tx, _ = Transaction.deserialize(bytes.fromhex(ent['data']))
                txs.append(tx)

            prev_hex = template['previousblockhash']
            prev_blk = self.node.chainstate.get_block(prev_hex)
            prev_hash_bytes = prev_blk.header.hash() if prev_blk else bytes.fromhex(prev_hex)[::-1]

            mr = bytes.fromhex(template['merkleroot'])
            bits = int(template['bits'], 16) if isinstance(template['bits'], str) else int(template['bits'])

            header = BlockHeader(
                version=int(template['version']),
                prev_block_hash=prev_hash_bytes,
                merkle_root=mr,
                timestamp=int(template['curtime']),
                bits=bits,
                nonce=0
            )

            return Block(header, txs)
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"_create_block_from_template: {e}")
            return None

    async def _broadcast_block_inv(self, block_hash_hex: str) -> None:
        if not self.node.connman:
            return
        bh = bytes.fromhex(block_hash_hex)[::-1]
        inv = InvMessage(inventory=[(InvMessage.InvType.MSG_BLOCK, bh)])
        await self.node.connman.broadcast('inv', inv.serialize())
