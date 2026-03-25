"""Block template creation for mining."""

import time
from typing import List, Optional, Dict, Any

from shared.core.block import BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.core.merkle import merkle_root
from shared.consensus.subsidy import get_block_subsidy
from shared.consensus.weights import calculate_transaction_weight
from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.mempool.pool import Mempool

logger = get_logger()


class BlockAssembler:
    """Block template assembler for mining."""

    def __init__(
        self,
        chainstate: ChainState,
        mempool: Mempool,
        coinbase_address: Optional[str] = None,
        network: Optional[str] = None,
    ):
        self.chainstate = chainstate
        self.mempool = mempool
        self.coinbase_address = coinbase_address
        self.network = network or getattr(chainstate, 'network', 'mainnet')

        self.max_weight = chainstate.params.max_block_weight
        self.max_sigops = chainstate.params.max_block_sigops
        self.reserved_weight = 4000

    def _tip_header(self) -> Optional[BlockHeader]:
        h = self.chainstate.get_best_height()
        if h < 0:
            return None
        return self.chainstate.get_header(h)

    async def create_block_template(self, address: Optional[str] = None) -> Dict[str, Any]:
        tip_height = self.chainstate.get_best_height()
        tip_hash = self.chainstate.get_best_block_hash()
        tip_header = self._tip_header()

        if not tip_header or not tip_hash:
            raise ValueError("No chain tip found")

        height = tip_height + 1

        transactions = await self._select_transactions()

        subsidy = get_block_subsidy(height, self.chainstate.params)
        total_fees = sum(self._get_transaction_fee(tx) for tx in transactions)
        coinbase_value = subsidy + total_fees

        reward_addr = address or self.coinbase_address
        if not reward_addr:
            raise ValueError("No coinbase address provided")

        coinbase_tx = self._create_coinbase(height, coinbase_value, reward_addr)

        all_txs = [coinbase_tx] + transactions
        tx_hashes = [tx.txid() for tx in all_txs]
        merkle_root_hash = merkle_root(tx_hashes)
        if merkle_root_hash is None:
            merkle_root_hash = b'\x00' * 32

        bits = await self._get_next_bits()
        timestamp = self._calculate_timestamp(tip_header)

        header = BlockHeader(
            version=0x20000000,
            prev_block_hash=tip_header.hash(),
            merkle_root=merkle_root_hash,
            timestamp=timestamp,
            bits=bits,
            nonce=0,
        )

        return {
            'version': header.version,
            'previousblockhash': tip_hash,
            'height': height,
            'transactions': self._serialize_transactions(transactions),
            'coinbaseaux': {},
            'coinbasevalue': coinbase_value,
            'coinbase_tx': coinbase_tx.serialize().hex(),
            'target': hex(bits),
            'mintime': timestamp,
            'mutable': ['time', 'transactions', 'prevblock'],
            'noncerange': '00000000ffffffff',
            'sigoplimit': self.max_sigops,
            'sizelimit': self.chainstate.params.max_block_size,
            'weightlimit': self.max_weight,
            'curtime': timestamp,
            'bits': hex(bits),
            'header': header.serialize().hex(),
            'merkleroot': merkle_root_hash.hex(),
            'witnessmerkleroot': merkle_root_hash.hex(),
        }

    async def _select_transactions(self) -> List[Transaction]:
        if not self.mempool:
            return []

        all_txs = await self.mempool.get_transactions()

        txs_with_fees: List[tuple] = []
        for tx in all_txs:
            fee = self._mempool_fee(tx) or self._get_transaction_fee(tx)
            raw = tx.serialize()
            size = len(raw)
            if size > 0:
                txs_with_fees.append((tx, fee / size))

        txs_with_fees.sort(key=lambda x: x[1], reverse=True)

        selected: List[Transaction] = []
        current_weight = self.reserved_weight

        for tx, _ in txs_with_fees:
            tx_weight = calculate_transaction_weight(tx)

            if current_weight + tx_weight <= self.max_weight:
                if await self._ancestors_included(tx, selected):
                    selected.append(tx)
                    current_weight += tx_weight

        return selected

    def _mempool_fee(self, tx: Transaction) -> Optional[int]:
        tid = tx.txid().hex()
        ent = self.mempool.transactions.get(tid) if self.mempool else None
        return ent.fee if ent else None

    def _create_coinbase(self, height: int, value: int, address: str) -> Transaction:
        from node.wallet.core.tx_builder import TransactionBuilder

        builder = TransactionBuilder(self.network)

        height_bytes = height.to_bytes(max(1, (height.bit_length() + 7) // 8), 'little')
        coinbase_script = bytes([len(height_bytes)]) + height_bytes + b"/BerzCoin/"

        txin = TxIn(
            prev_tx_hash=b'\x00' * 32,
            prev_tx_index=0xffffffff,
            script_sig=coinbase_script,
            sequence=0xffffffff,
        )

        script_pubkey = builder._create_script_pubkey(address)
        txout = TxOut(value=value, script_pubkey=script_pubkey)

        tx = Transaction(version=1)
        tx.vin.append(txin)
        tx.vout.append(txout)
        return tx

    def _get_transaction_fee(self, tx: Transaction) -> int:
        total_output = sum(txout.value for txout in tx.vout)
        total_input = 0

        for txin in tx.vin:
            utxo = self.chainstate.get_utxo(txin.prev_tx_hash.hex(), txin.prev_tx_index)
            if utxo:
                total_input += int(utxo['value'])

        return total_input - total_output

    async def _ancestors_included(self, tx: Transaction, selected: List[Transaction]) -> bool:
        txid = tx.txid().hex()
        entry = self.mempool.transactions.get(txid) if self.mempool else None

        if not entry or not entry.ancestors:
            return True

        selected_txids = {t.txid().hex() for t in selected}

        for ancestor in entry.ancestors:
            if ancestor not in selected_txids:
                return False

        return True

    def _serialize_transactions(self, transactions: List[Transaction]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for tx in transactions:
            result.append({
                'data': tx.serialize().hex(),
                'txid': tx.txid().hex(),
                'hash': tx.txid().hex(),
                'fee': self._mempool_fee(tx) or self._get_transaction_fee(tx),
                'weight': calculate_transaction_weight(tx),
            })
        return result

    async def _get_next_bits(self) -> int:
        pow_check = ProofOfWork(self.chainstate.params)
        best_height = self.chainstate.get_best_height()

        headers: List[BlockHeader] = []
        interval = self.chainstate.params.retarget_interval_blocks()
        start = max(0, best_height - (interval - 1))

        for h in range(start, best_height + 1):
            header = self.chainstate.get_header(h)
            if header:
                headers.append(header)

        if len(headers) < interval:
            return headers[-1].bits if headers else self.chainstate.params.genesis_bits

        return pow_check.get_next_work_required(headers, best_height)

    def _median_time_past(self, up_to_height: int) -> int:
        times: List[int] = []
        for h in range(max(0, up_to_height - 10), up_to_height + 1):
            blk = self.chainstate.get_block_by_height(h)
            if blk:
                times.append(blk.header.timestamp)
        if not times:
            return int(time.time())
        times.sort()
        return times[len(times) // 2]

    def _calculate_timestamp(self, prev_header: BlockHeader) -> int:
        now = int(time.time())
        tip_h = self.chainstate.get_best_height()
        median_time = self._median_time_past(tip_h)

        timestamp = max(now, median_time + 1)

        if timestamp <= prev_header.timestamp:
            timestamp = prev_header.timestamp + 1

        return timestamp

    def get_mining_info(self) -> Dict[str, Any]:
        best_height = self.chainstate.get_best_height()
        best_header = self._tip_header()

        if not best_header:
            return {'error': 'No blocks yet'}

        return {
            'blocks': best_height,
            'current_block_weight': 0,
            'current_block_tx': 0,
            'difficulty': self._calculate_difficulty(best_header.bits),
            'network_hashps': self._estimate_network_hashrate(),
            'pooledtx': len(self.mempool.transactions) if self.mempool else 0,
            'chain': self.network,
        }

    def _calculate_difficulty(self, bits: int) -> float:
        pow_check = ProofOfWork(self.chainstate.params)
        return pow_check.calculate_difficulty(bits)

    def _estimate_network_hashrate(self) -> float:
        best_height = self.chainstate.get_best_height()

        if best_height < 120:
            return 0.0

        start_height = best_height - 119
        start_time: Optional[int] = None
        end_time: Optional[int] = None

        for h in range(start_height, best_height + 1):
            block = self.chainstate.get_block_by_height(h)
            if block:
                if start_time is None:
                    start_time = block.header.timestamp
                end_time = block.header.timestamp

        if start_time is None or end_time is None:
            return 0.0

        time_span = end_time - start_time
        if time_span <= 0:
            return 0.0

        total_work = 0
        for h in range(start_height, best_height + 1):
            hdr = self.chainstate.get_header(h)
            if hdr:
                total_work += self.chainstate.chainwork.calculate_block_work(hdr.bits)

        return total_work / time_span
