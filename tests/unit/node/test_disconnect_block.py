"""Regression tests for block disconnect rollback behavior."""

import unittest
from contextlib import contextmanager

from node.validation.disconnect import DisconnectBlock


class _Header:
    def __init__(self, block_hash: str, prev_hash: str):
        self._block_hash = block_hash
        self.prev_block_hash = bytes.fromhex(prev_hash)

    def hash_hex(self) -> str:
        return self._block_hash


class _TxIn:
    def __init__(self, prev_txid: str, prev_index: int):
        self.prev_tx_hash = bytes.fromhex(prev_txid)
        self.prev_tx_index = int(prev_index)


class _Tx:
    def __init__(self, txid_hex: str, vin, vout_count: int, is_coinbase: bool = False):
        self._txid_hex = txid_hex
        self.vin = vin
        self.vout = [object() for _ in range(vout_count)]
        self._is_coinbase = is_coinbase

    def txid(self) -> bytes:
        return bytes.fromhex(self._txid_hex)

    def is_coinbase(self) -> bool:
        return self._is_coinbase


class _Block:
    def __init__(self, block_hash: str, prev_hash: str, txs):
        self.header = _Header(block_hash, prev_hash)
        self.transactions = list(txs)


class _DBStub:
    def __init__(self, outputs):
        self.outputs = dict(outputs)
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

    def fetch_one(self, query, params):
        _ = query
        txid, index = params
        row = self.outputs.get((txid, int(index)))
        if row is None:
            return None
        return {
            "value": row["value"],
            "script_pubkey": row["script_pubkey"],
            "height": row["height"],
            "is_coinbase": row["is_coinbase"],
        }

    def execute(self, query, params=()):
        q = " ".join(query.split()).lower()
        if "chain_state" in q:
            raise AssertionError("disconnect must not write to non-existent chain_state table")
        if "update outputs" in q and "set spent = 0" in q:
            txid, index = params
            key = (txid, int(index))
            if key in self.outputs:
                self.outputs[key]["spent"] = 0
                self.outputs[key]["spent_by_txid"] = None
                self.outputs[key]["spent_by_index"] = None


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

    def remove_utxo(self, txid, index):
        return self.utxos.pop((txid, int(index)), None) is not None


class _Entry:
    def __init__(self, height: int, block_hash: str, prev_hash: str):
        self.height = int(height)
        self.block_hash = block_hash
        self.header = _Header(block_hash, prev_hash)


class _BlockIndexStub:
    def __init__(self, entries):
        self.entries = dict(entries)
        self.main_chain_marks = {}

    def get_height(self, block_hash: str):
        entry = self.entries.get(block_hash)
        return entry.height if entry else None

    def mark_main_chain(self, block_hash: str, is_main: bool = True):
        self.main_chain_marks[block_hash] = bool(is_main)

    def get_best_hash(self):
        best = None
        for entry in self.entries.values():
            if best is None or entry.height > best.height:
                best = entry
        return best.block_hash if best else None

    def get_block(self, block_hash: str):
        return self.entries.get(block_hash)

    def set_best_chain_tip(self, block_hash: str):
        _ = block_hash


class TestDisconnectBlock(unittest.TestCase):
    def test_disconnect_restores_spent_outputs_and_clears_created_utxos(self):
        prev_txid = "aa" * 32
        spending_txid = "bb" * 32
        block_hash = "cc" * 32
        prev_hash = "dd" * 32

        db = _DBStub(
            {
                (prev_txid, 0): {
                    "value": 5000,
                    "script_pubkey": b"\x51",
                    "height": 7,
                    "is_coinbase": 1,
                    "spent": 1,
                    "spent_by_txid": spending_txid,
                    "spent_by_index": 0,
                },
                (spending_txid, 0): {
                    "value": 4900,
                    "script_pubkey": b"\x51",
                    "height": 8,
                    "is_coinbase": 0,
                    "spent": 0,
                    "spent_by_txid": None,
                    "spent_by_index": None,
                },
            }
        )
        utxo = _UTXOStoreStub(db)
        utxo.utxos[(spending_txid, 0)] = {
            "value": 4900,
            "script_pubkey": b"\x51",
            "height": 8,
            "is_coinbase": False,
        }

        idx = _BlockIndexStub(
            {
                block_hash: _Entry(8, block_hash, prev_hash),
                prev_hash: _Entry(7, prev_hash, "00" * 32),
            }
        )

        tx = _Tx(
            spending_txid,
            vin=[_TxIn(prev_txid, 0)],
            vout_count=1,
            is_coinbase=False,
        )
        block = _Block(block_hash, prev_hash, [tx])

        disconnector = DisconnectBlock(utxo, idx)
        ok = disconnector.disconnect(block)
        self.assertTrue(ok)

        self.assertIn((prev_txid, 0), utxo.utxos)
        restored = utxo.utxos[(prev_txid, 0)]
        self.assertEqual(restored["height"], 7)
        self.assertTrue(restored["is_coinbase"])
        self.assertNotIn((spending_txid, 0), utxo.utxos)
        self.assertEqual(db.outputs[(prev_txid, 0)]["spent"], 0)
        self.assertIsNone(db.outputs[(prev_txid, 0)]["spent_by_txid"])
        self.assertEqual(idx.main_chain_marks.get(block_hash), False)


if __name__ == "__main__":
    unittest.main()
