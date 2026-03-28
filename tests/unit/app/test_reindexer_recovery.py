"""Recovery-focused tests for reindex replay behavior."""

import asyncio
import unittest
from contextlib import contextmanager

from node.app.reindex import Reindexer


class _TxIn:
    def __init__(self, prev_txid: str, prev_index: int):
        self.prev_tx_hash = bytes.fromhex(prev_txid)
        self.prev_tx_index = prev_index


class _TxOut:
    def __init__(self, value: int, script_pubkey: bytes):
        self.value = value
        self.script_pubkey = script_pubkey


class _Tx:
    def __init__(self, txid_hex: str, vin, vout, is_coinbase: bool):
        self._txid_hex = txid_hex
        self.vin = vin
        self.vout = vout
        self._is_coinbase = is_coinbase

    def txid(self) -> bytes:
        return bytes.fromhex(self._txid_hex)

    def is_coinbase(self) -> bool:
        return self._is_coinbase


class _Block:
    def __init__(self, transactions):
        self.transactions = transactions


class _DBStub:
    def __init__(self):
        self.queries = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise

    def execute(self, query: str, params=()):
        self.queries.append((query, params))


class _UTXOStoreStub:
    def __init__(self, db: _DBStub):
        self.db = db
        self.utxos = {}

    def add_utxo(self, txid, index, value, script_pubkey, height, is_coinbase):
        self.utxos[(txid, int(index))] = {
            "value": int(value),
            "script_pubkey": script_pubkey,
            "height": int(height),
            "is_coinbase": bool(is_coinbase),
        }

    def spend_utxo(self, txid, index, spent_by_txid=None, spent_by_index=None):
        return self.utxos.pop((txid, int(index)), None) is not None

    def verify_consistency(self):
        return True


class _BlocksStoreSentinel:
    def read_block(self, _height):
        raise AssertionError("Reindexer should replay from chainstate main-chain view")


class _ChainStateStub:
    def __init__(self, blocks_by_height, best_height):
        self._blocks = dict(blocks_by_height)
        self._best_height = int(best_height)
        self.calls = []

    def get_best_height(self):
        return self._best_height

    def get_block_by_height(self, height: int):
        self.calls.append(int(height))
        return self._blocks.get(int(height))


class TestReindexerRecovery(unittest.TestCase):
    def test_reindex_forces_genesis_replay_for_partial_request(self):
        async def run():
            tx0id = "01" * 32
            tx1id = "02" * 32

            tx0 = _Tx(tx0id, vin=[], vout=[_TxOut(100, b"\x51")], is_coinbase=True)
            tx1 = _Tx(
                tx1id,
                vin=[_TxIn(tx0id, 0)],
                vout=[_TxOut(90, b"\x51")],
                is_coinbase=False,
            )

            chainstate = _ChainStateStub(
                {
                    0: _Block([tx0]),
                    1: _Block([tx1]),
                },
                best_height=1,
            )
            db = _DBStub()
            utxo = _UTXOStoreStub(db)
            reindexer = Reindexer(chainstate, _BlocksStoreSentinel(), utxo)

            ok = await reindexer.run(start_height=1, end_height=1)
            self.assertTrue(ok)
            self.assertEqual(chainstate.calls, [0, 1])
            self.assertNotIn((tx0id, 0), utxo.utxos)
            self.assertIn((tx1id, 0), utxo.utxos)

        asyncio.run(run())

    def test_reindex_fails_on_missing_main_chain_block(self):
        async def run():
            tx0id = "03" * 32
            tx0 = _Tx(tx0id, vin=[], vout=[_TxOut(50, b"\x51")], is_coinbase=True)
            chainstate = _ChainStateStub({0: _Block([tx0])}, best_height=1)
            db = _DBStub()
            utxo = _UTXOStoreStub(db)
            reindexer = Reindexer(chainstate, _BlocksStoreSentinel(), utxo)

            ok = await reindexer.run(0, 1)
            self.assertFalse(ok)
            self.assertEqual(db.rollbacks, 1)

        asyncio.run(run())

    def test_reindex_fails_when_input_cannot_be_spent(self):
        async def run():
            unknown_prev = "04" * 32
            tx1 = _Tx(
                "05" * 32,
                vin=[_TxIn(unknown_prev, 0)],
                vout=[_TxOut(10, b"\x51")],
                is_coinbase=False,
            )
            chainstate = _ChainStateStub({0: _Block([tx1])}, best_height=0)
            db = _DBStub()
            utxo = _UTXOStoreStub(db)
            reindexer = Reindexer(chainstate, _BlocksStoreSentinel(), utxo)

            ok = await reindexer.run(0, 0)
            self.assertFalse(ok)
            self.assertEqual(db.rollbacks, 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
