"""Mining reliability tests for selection invariants and canonical block acceptance path."""

from __future__ import annotations

import asyncio
import time
import unittest
from typing import Optional

from node.mining.miner import MiningNode
from shared.consensus.params import ConsensusParams
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.address import public_key_to_address


def _coinbase(tag: bytes = b"\x02\x01") -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [
        TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=tag,
            sequence=0xFFFFFFFF,
        )
    ]
    tx.vout = [TxOut(value=50_000, script_pubkey=b"\x51")]
    return tx


def _tip_block() -> Block:
    cb = _coinbase()
    mr = merkle_root([cb.txid()]) or (b"\x00" * 32)
    h = BlockHeader(
        version=1,
        prev_block_hash=b"\x00" * 32,
        merkle_root=mr,
        timestamp=int(time.time()) - 120,
        bits=0x207FFFFF,
        nonce=0,
    )
    return Block(h, [cb])


class _ChainState:
    def __init__(self):
        self.params = ConsensusParams.regtest()
        self.network = "regtest"
        self._tip = _tip_block()

    def get_best_height(self):
        return 0

    def get_best_block_hash(self):
        return self._tip.header.hash_hex()

    def get_header(self, _height):
        return self._tip.header

    def get_block_by_height(self, _height):
        return self._tip

    def get_utxo(self, _txid, _index):
        return {"value": 100_000}


class _Mempool:
    def __init__(self, txs=None):
        self._txs = list(txs or [])
        self.unconfirmed_parents = {}

    async def get_transactions_for_block(self, max_weight: int):
        _ = max_weight
        return list(self._txs)

    async def remove_transaction(self, _txid: str, include_descendants: bool = True):
        _ = include_descendants
        return []


def _spend(prev: bytes, idx: int, value: int = 99_000) -> Transaction:
    tx = Transaction(version=2)
    tx.vin = [TxIn(prev_tx_hash=prev, prev_tx_index=idx, script_sig=b"", sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(value=value, script_pubkey=b"\x51")]
    return tx


class TestMiningReliability(unittest.TestCase):
    def test_miner_rejects_conflicting_selected_transactions(self) -> None:
        async def run():
            chain = _ChainState()
            prev = b"\x11" * 32
            tx1 = _spend(prev, 0)
            tx2 = _spend(prev, 0)  # same outpoint conflict
            mempool = _Mempool([tx1, tx2])
            address = public_key_to_address(PrivateKey().public_key())
            miner = MiningNode(chain, mempool, address)
            block = await miner.mine_single_block(address)
            self.assertIsNone(block)

        asyncio.run(run())

    def test_invalid_mined_coinbase_is_rejected_by_acceptor_path(self) -> None:
        async def run():
            chain = _ChainState()
            mempool = _Mempool([])
            address = public_key_to_address(PrivateKey().public_key())

            async def rejector(_block, _source, _relay):
                return False, "", "stateful_validation_failed"

            miner = MiningNode(chain, mempool, address, block_acceptor=rejector)
            ok = await miner._submit_block(_tip_block())
            self.assertFalse(ok)

        asyncio.run(run())

    def test_mined_block_uses_same_accept_path_as_peer(self) -> None:
        async def run():
            chain = _ChainState()
            mempool = _Mempool([])
            address = public_key_to_address(PrivateKey().public_key())
            called = {"count": 0}

            async def acceptor(_block, _source, _relay):
                called["count"] += 1
                return True, "aa" * 32, None

            miner = MiningNode(chain, mempool, address, block_acceptor=acceptor)
            ok = await miner._submit_block(_tip_block())
            self.assertTrue(ok)
            self.assertEqual(called["count"], 1)

        asyncio.run(run())

    def test_mining_works_after_restart_and_datadir_reload_simulation(self) -> None:
        async def run():
            chain = _ChainState()
            mempool = _Mempool([])
            address = public_key_to_address(PrivateKey().public_key())
            miner1 = MiningNode(chain, mempool, address)
            b1 = await miner1.mine_single_block(address)
            self.assertIsNotNone(b1)

            # Simulate process restart/datadir reload by rebuilding miner+chainstate objects.
            chain2 = _ChainState()
            miner2 = MiningNode(chain2, _Mempool([]), address)
            b2 = await miner2.mine_single_block(address)
            self.assertIsNotNone(b2)

        asyncio.run(run())

    def test_mining_works_after_wallet_reactivation_and_address_reset(self) -> None:
        async def run():
            chain = _ChainState()
            mempool = _Mempool([])
            active_addr = public_key_to_address(PrivateKey().public_key())
            old_addr = public_key_to_address(PrivateKey().public_key())
            wallet_state = {"active": active_addr, "addr": old_addr}

            def guard(addr: str) -> bool:
                return addr == wallet_state["active"]

            miner = MiningNode(chain, mempool, wallet_state["addr"], address_guard=guard)
            await miner.start_mining()
            self.assertFalse(miner.is_mining)
            self.assertEqual(miner.last_stop_reason, "mining_address_wallet_mismatch")

            wallet_state["addr"] = wallet_state["active"]
            miner.mining_address = wallet_state["addr"]
            await miner.start_mining()
            self.assertTrue(miner.is_mining)
            await miner.stop_mining()

        asyncio.run(run())

    def test_mined_block_confirms_mempool_transactions_via_shared_accept_path(self) -> None:
        async def run():
            chain = _ChainState()
            tx = _spend(b"\x22" * 32, 0)
            mempool = _Mempool([tx])
            address = public_key_to_address(PrivateKey().public_key())
            state = {"removed": False}

            async def acceptor(block: Block, _source: Optional[str], _relay: bool):
                # Simulate node.on_block shared path behavior: confirmed txs are removed.
                if len(block.transactions) > 1:
                    state["removed"] = True
                return True, block.header.hash_hex(), None

            miner = MiningNode(chain, mempool, address, block_acceptor=acceptor)
            block = await miner.mine_single_block(address)
            self.assertIsNotNone(block)
            ok = await miner._submit_block(block)
            self.assertTrue(ok)
            self.assertTrue(state["removed"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
