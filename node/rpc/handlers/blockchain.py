"""Blockchain RPC handlers."""

from typing import Any, Dict, List, Optional

from shared.consensus.pow import ProofOfWork
from shared.utils.logging import get_logger

logger = get_logger()


class BlockchainHandlers:
    """RPC handlers for blockchain queries."""

    def __init__(self, node: Any):
        self.node = node

    def _best_header(self):
        chain = self.node.chainstate
        h = chain.get_best_height()
        if h < 0:
            return None
        return chain.get_header(h)

    async def get_blockchain_info(self) -> Dict[str, Any]:
        chain = self.node.chainstate
        best_h = chain.get_best_height()

        return {
            'chain': getattr(self.node, 'network', 'mainnet'),
            'blocks': best_h,
            'headers': best_h,
            'bestblockhash': chain.get_best_block_hash(),
            'difficulty': self._get_difficulty(),
            'mediantime': self._get_median_time(),
            'verificationprogress': 1.0,
            'initialblockdownload': best_h < 1000,
            'chainwork': str(chain.get_best_chainwork()),
            'size_on_disk': self._get_chain_size(),
            'pruned': False,
            'warnings': ''
        }

    async def get_block(self, block_hash: str, verbosity: int = 1) -> Any:
        chain = self.node.chainstate
        block = chain.get_block(block_hash)

        if not block:
            return {'error': f'Block not found: {block_hash}'}

        height = chain.get_height(block_hash)
        if height is None:
            height = -1

        if verbosity == 0:
            return {'hex': block.serialize().hex()}

        result: Dict[str, Any] = {
            'hash': block_hash,
            'confirmations': max(0, chain.get_best_height() - height + 1) if height >= 0 else 0,
            'height': height,
            'version': block.header.version,
            'versionHex': hex(block.header.version),
            'merkleroot': block.header.merkle_root.hex(),
            'time': block.header.timestamp,
            'mediantime': self._get_median_time_at_height(height),
            'nonce': block.header.nonce,
            'bits': hex(block.header.bits),
            'difficulty': self._calculate_difficulty(block.header.bits),
            'chainwork': '0',
            'nTx': len(block.transactions),
            'previousblockhash': block.header.prev_block_hash[::-1].hex(),
            'nextblockhash': await self._get_next_block_hash(height)
        }

        if verbosity >= 2:
            result['tx'] = []
            for tx in block.transactions:
                result['tx'].append({
                    'txid': tx.txid().hex(),
                    'version': tx.version,
                    'locktime': tx.locktime,
                    'vin': [{
                        'txid': txin.prev_tx_hash.hex(),
                        'vout': txin.prev_tx_index,
                        'scriptSig': {'hex': txin.script_sig.hex()},
                        'sequence': txin.sequence
                    } for txin in tx.vin],
                    'vout': [{
                        'value': txout.value / 100000000,
                        'n': i,
                        'scriptPubKey': {'hex': txout.script_pubkey.hex()}
                    } for i, txout in enumerate(tx.vout)]
                })

        return result

    async def get_block_header(self, block_hash: str) -> Dict[str, Any]:
        chain = self.node.chainstate
        header = chain.get_header_by_hash(block_hash)

        if not header:
            return {'error': f'Block header not found: {block_hash}'}

        height = chain.get_height(block_hash)
        if height is None:
            height = -1

        return {
            'hash': block_hash,
            'confirmations': max(0, chain.get_best_height() - height + 1) if height >= 0 else 0,
            'height': height,
            'version': header.version,
            'versionHex': hex(header.version),
            'merkleroot': header.merkle_root.hex(),
            'time': header.timestamp,
            'mediantime': self._get_median_time_at_height(height),
            'nonce': header.nonce,
            'bits': hex(header.bits),
            'difficulty': self._calculate_difficulty(header.bits),
            'previousblockhash': header.prev_block_hash[::-1].hex(),
            'nextblockhash': await self._get_next_block_hash(height)
        }

    async def get_best_block_hash(self) -> Optional[str]:
        return self.node.chainstate.get_best_block_hash()

    async def get_block_count(self) -> int:
        return max(0, self.node.chainstate.get_best_height() + 1)

    async def get_block_hash(self, height: int) -> str:
        chain = self.node.chainstate
        block = chain.get_block_by_height(height)

        if not block:
            raise ValueError(f'Block not found at height {height}')

        return block.header.hash_hex()

    async def get_block_stats(self, block_hash: str) -> Dict[str, Any]:
        chain = self.node.chainstate
        block = chain.get_block(block_hash)

        if not block:
            return {'error': f'Block not found: {block_hash}'}

        h = chain.get_height(block_hash)

        return {
            'avgfee': 0,
            'avgfeerate': 0,
            'avgtxsize': 0,
            'blockhash': block_hash,
            'height': h,
            'ins': sum(len(tx.vin) for tx in block.transactions),
            'maxfee': 0,
            'maxfeerate': 0,
            'maxtxsize': 0,
            'medianfee': 0,
            'mediantime': block.header.timestamp,
            'mediantxsize': 0,
            'minfee': 0,
            'minfeerate': 0,
            'mintxsize': 0,
            'outs': sum(len(tx.vout) for tx in block.transactions),
            'subsidy': 0,
            'swtotal_size': 0,
            'swtotal_weight': 0,
            'swtxs': 0,
            'time': block.header.timestamp,
            'total_out': sum(sum(txout.value for txout in tx.vout) for tx in block.transactions),
            'total_size': block.size(),
            'total_weight': block.weight(),
            'txs': len(block.transactions),
            'utxo_increase': 0,
            'utxo_size_inc': 0
        }

    async def get_chaintips(self) -> List[Dict[str, Any]]:
        chain = self.node.chainstate
        best_height = chain.get_best_height()
        best_hash = chain.get_best_block_hash()

        return [{
            'height': best_height,
            'hash': best_hash,
            'branchlen': 0,
            'status': 'active'
        }]

    async def get_tx_out(self, txid: str, vout: int, include_mempool: bool = True) -> Optional[Dict[str, Any]]:
        chain = self.node.chainstate

        utxo = chain.get_utxo(txid, vout)

        if utxo:
            spk = utxo['script_pubkey']
            if isinstance(spk, memoryview):
                spk = spk.tobytes()
            return {
                'bestblock': chain.get_best_block_hash(),
                'confirmations': chain.get_best_height() - int(utxo['height']) + 1,
                'value': utxo['value'] / 100000000,
                'scriptPubKey': {
                    'hex': spk.hex() if isinstance(spk, (bytes, bytearray)) else str(spk)
                },
                'coinbase': bool(utxo['is_coinbase'])
            }

        if include_mempool and getattr(self.node, 'mempool', None):
            tx = await self.node.mempool.get_transaction(txid)
            if tx and vout < len(tx.vout):
                return {
                    'bestblock': chain.get_best_block_hash(),
                    'confirmations': 0,
                    'value': tx.vout[vout].value / 100000000,
                    'scriptPubKey': {
                        'hex': tx.vout[vout].script_pubkey.hex()
                    },
                    'coinbase': False
                }

        return None

    def _get_difficulty(self) -> float:
        hdr = self._best_header()
        if not hdr:
            return 1.0
        return self._calculate_difficulty(hdr.bits)

    def _calculate_difficulty(self, bits: int) -> float:
        pow_check = ProofOfWork(self.node.chainstate.params)
        return pow_check.calculate_difficulty(bits)

    def _get_median_time(self) -> int:
        chain = self.node.chainstate
        best_height = chain.get_best_height()

        times = []
        for h in range(max(0, best_height - 10), best_height + 1):
            block = chain.get_block_by_height(h)
            if block:
                times.append(block.header.timestamp)

        if not times:
            return 0

        times.sort()
        return times[len(times) // 2]

    def _get_median_time_at_height(self, height: int) -> int:
        chain = self.node.chainstate

        times = []
        for h in range(max(0, height - 10), height + 1):
            block = chain.get_block_by_height(h)
            if block:
                times.append(block.header.timestamp)

        if not times:
            return 0

        times.sort()
        return times[len(times) // 2]

    async def _get_next_block_hash(self, height: int) -> Optional[str]:
        chain = self.node.chainstate
        next_block = chain.get_block_by_height(height + 1)

        if next_block:
            return next_block.header.hash_hex()

        return None

    def _get_chain_size(self) -> int:
        return 0
