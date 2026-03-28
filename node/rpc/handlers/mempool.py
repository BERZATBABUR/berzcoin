"""Mempool RPC handlers."""

from typing import Any, Dict, List, Optional

from shared.core.transaction import Transaction
from shared.protocol.messages import InvMessage
from shared.utils.logging import get_logger

logger = get_logger()


class MempoolHandlers:
    """RPC handlers for mempool queries."""

    def __init__(self, node: Any):
        self.node = node

    async def get_mempool_info(self) -> Dict[str, Any]:
        if not getattr(self.node, 'mempool', None):
            return {'error': 'Mempool not initialized'}

        stats = await self.node.mempool.get_stats()

        return {
            'loaded': True,
            'size': stats['size'],
            'bytes': stats['total_size'],
            'usage': stats['total_weight'],
            'maxmempool': 300000000,
            'mempoolminfee': 0.00001,
            'minrelaytxfee': 0.00001,
            'unbroadcastcount': 0
        }

    async def get_raw_mempool(self, verbose: bool = False) -> Any:
        if not getattr(self.node, 'mempool', None):
            return []

        txs = await self.node.mempool.get_transactions()

        if not verbose:
            return [tx.txid().hex() for tx in txs]

        result: Dict[str, Any] = {}
        for tx in txs:
            txid = tx.txid().hex()
            entry = self.node.mempool.transactions.get(txid)

            if entry:
                desc_sz = sum(
                    self.node.mempool.transactions[d].size
                    for d in entry.descendants if d in self.node.mempool.transactions
                )
                anc_sz = sum(
                    self.node.mempool.transactions[a].size
                    for a in entry.ancestors if a in self.node.mempool.transactions
                )
                result[txid] = {
                    'size': entry.size,
                    'fee': entry.fee,
                    'modifiedfee': entry.fee,
                    'time': entry.time_added,
                    'height': entry.height_added,
                    'descendantcount': len(entry.descendants),
                    'descendantsize': desc_sz,
                    'ancestorcount': len(entry.ancestors),
                    'ancestorsize': anc_sz,
                    'wtxid': tx.wtxid().hex()
                }

        return result

    async def get_mempool_entry(self, txid: str) -> Optional[Dict[str, Any]]:
        if not getattr(self.node, 'mempool', None):
            return None

        tx = await self.node.mempool.get_transaction(txid)
        if not tx:
            return None

        entry = self.node.mempool.transactions.get(txid)
        if not entry:
            return None

        desc_sz = sum(
            self.node.mempool.transactions[d].size
            for d in entry.descendants if d in self.node.mempool.transactions
        )
        anc_sz = sum(
            self.node.mempool.transactions[a].size
            for a in entry.ancestors if a in self.node.mempool.transactions
        )

        return {
            'size': entry.size,
            'fee': entry.fee,
            'modifiedfee': entry.fee,
            'time': entry.time_added,
            'height': entry.height_added,
            'descendantcount': len(entry.descendants),
            'descendantsize': desc_sz,
            'ancestorcount': len(entry.ancestors),
            'ancestorsize': anc_sz,
            'wtxid': tx.wtxid().hex()
        }

    async def send_raw_transaction(self, hex_string: str, allow_high_fees: bool = False) -> str:
        _ = allow_high_fees
        tx_bytes = bytes.fromhex(hex_string.strip())
        tx, _ = Transaction.deserialize(tx_bytes)

        if hasattr(self.node, "on_transaction"):
            accepted, txid, reason = await self.node.on_transaction(tx, relay=True)
            if accepted:
                return txid
            raise ValueError(f"Transaction rejected: {reason}")

        if await self.node.mempool.add_transaction(tx):
            if self.node.connman:
                inv = InvMessage(inventory=[(InvMessage.InvType.MSG_TX, tx.txid())])
                await self.node.connman.broadcast('inv', inv.serialize())
            return tx.txid().hex()
        raise ValueError('Transaction rejected by mempool')

    async def test_mempool_accept(self, raw_txs: List[str], max_fee_rate: int = 0) -> List[Dict[str, Any]]:
        _ = max_fee_rate
        results: List[Dict[str, Any]] = []

        if not getattr(self.node, 'mempool', None):
            return [{'txid': 'unknown', 'allowed': False, 'reject-reason': 'no mempool'}]

        for raw_tx in raw_txs:
            try:
                tx_bytes = bytes.fromhex(raw_tx.strip())
                tx, _ = Transaction.deserialize(tx_bytes)
                tid = tx.txid().hex()

                if await self.node.mempool.get_transaction(tid):
                    results.append({
                        'txid': tid,
                        'allowed': False,
                        'reject-reason': 'already in mempool'
                    })
                    continue

                is_valid = await self.node.mempool._validate_transaction(tx)

                if is_valid:
                    results.append({'txid': tid, 'allowed': True})
                else:
                    results.append({
                        'txid': tid,
                        'allowed': False,
                        'reject-reason': 'validation failed'
                    })

            except Exception as e:
                results.append({
                    'txid': 'unknown',
                    'allowed': False,
                    'reject-reason': str(e)
                })

        return results

    async def submit_package(self, raw_txs: List[str]) -> Dict[str, Any]:
        """Submit a package of related raw transactions atomically."""
        if not getattr(self.node, "mempool", None):
            return {"accepted": False, "reject-reason": "no mempool"}
        txs: List[Transaction] = []
        for raw_tx in raw_txs:
            tx_bytes = bytes.fromhex(raw_tx.strip())
            tx, _ = Transaction.deserialize(tx_bytes)
            txs.append(tx)
        result = await self.node.mempool.add_package(txs)
        if bool(result.get("accepted")) and self.node.connman and not hasattr(self.node, "on_transaction"):
            for tx in txs:
                inv = InvMessage(inventory=[(InvMessage.InvType.MSG_TX, tx.txid())])
                await self.node.connman.broadcast("inv", inv.serialize())
        return result
