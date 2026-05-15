"""Regression tests for atomic UTXO updates during block connect."""

import copy
import unittest
from contextlib import contextmanager

from node.validation.connect import ConnectBlock


class _Header:
    def __init__(self, block_hash: str):
        self._block_hash = block_hash

    def hash_hex(self) -> str:
        return self._block_hash


class _TxIn:
    def __init__(self, prev_txid: str, prev_index: int):
        self.prev_tx_hash = bytes.fromhex(prev_txid)
        self.prev_tx_index = int(prev_index)


class _TxOut:
    def __init__(self, value: int, script_pubkey: bytes):
        self.value = int(value)
        self.script_pubkey = bytes(script_pubkey)


class _Tx:
    def __init__(self, txid_hex: str, vin, vout, is_coinbase: bool = False):
        self._txid_hex = txid_hex
        self.vin = list(vin)
        self.vout = list(vout)
        self._is_coinbase = bool(is_coinbase)

    def txid(self) -> bytes:
        return bytes.fromhex(self._txid_hex)

    def is_coinbase(self) -> bool:
        return self._is_coinbase


class _Block:
    def __init__(self, block_hash: str, txs):
        self.header = _Header(block_hash)
        self.transactions = list(txs)


class _DBStub:
    def __init__(self):
        self.owner = None
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        if self.owner is None:
            raise RuntimeError("owner not attached")
        utxo_snapshot = copy.deepcopy(self.owner.utxos)
        outputs_snapshot = copy.deepcopy(self.owner.outputs_spent)
        try:
            yield self
            self.commits += 1
        except Exception:
            self.owner.utxos = utxo_snapshot
            self.owner.outputs_spent = outputs_snapshot
            self.rollbacks += 1
            raise

    def execute(self, _query, _params=()):
        return None


class _UTXOStoreStub:
    def __init__(self, db: _DBStub, utxos, fail_outpoints=None):
        self.db = db
        self.db.owner = self
        self.utxos = dict(utxos)
        self.outputs_spent = {}
        self.fail_outpoints = set(fail_outpoints or set())

    def spend_utxo(self, txid, index, spent_by_txid=None, spent_by_index=None):
        key = (str(txid), int(index))
        if key in self.fail_outpoints:
            return False
        if key not in self.utxos:
            return False
        self.utxos.pop(key, None)
        self.outputs_spent[key] = (spent_by_txid, spent_by_index)
        return True

    def get_utxo(self, txid, index):
        return self.utxos.get((str(txid), int(index)))

    def add_utxo(self, txid, index, value, script_pubkey, height, is_coinbase):
        self.utxos[(str(txid), int(index))] = {
            "value": int(value),
            "script_pubkey": bytes(script_pubkey),
            "height": int(height),
            "is_coinbase": bool(is_coinbase),
        }


class _BlockIndexStub:
    def __init__(self, block_hash: str, height: int):
        self._block_hash = block_hash
        self._height = int(height)
        self.main_chain_marks = []

    def get_height(self, block_hash):
        return self._height if block_hash == self._block_hash else None

    def mark_main_chain(self, block_hash, is_main=True):
        self.main_chain_marks.append((block_hash, bool(is_main)))


class TestConnectBlockAtomicity(unittest.TestCase):
    def test_connect_rolls_back_all_utxo_mutations_when_any_input_spend_fails(self):
        block_hash = "cc" * 32
        prev_a = "aa" * 32
        prev_b = "bb" * 32
        spend_txid = "dd" * 32

        db = _DBStub()
        utxo = _UTXOStoreStub(
            db,
            utxos={
                (prev_a, 0): {"value": 1000},
                (prev_b, 0): {"value": 2000},
            },
            fail_outpoints={(prev_b, 0)},
        )
        idx = _BlockIndexStub(block_hash, 10)
        connector = ConnectBlock(utxo, idx)

        tx = _Tx(
            spend_txid,
            vin=[_TxIn(prev_a, 0), _TxIn(prev_b, 0)],
            vout=[_TxOut(2500, b"\x51")],
            is_coinbase=False,
        )
        block = _Block(block_hash, [tx])
        before = copy.deepcopy(utxo.utxos)

        ok = connector.connect(block)
        self.assertFalse(ok)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(utxo.utxos, before)
        self.assertEqual(idx.main_chain_marks, [])


if __name__ == "__main__":
    unittest.main()
