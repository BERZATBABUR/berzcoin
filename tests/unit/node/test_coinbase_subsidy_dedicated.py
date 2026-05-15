"""Dedicated coinbase/subsidy coverage tests."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from node.chain.validation import BlockValidator
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore
from node.chain.block_index import BlockIndex
from node.validation.connect import ConnectBlock
from shared.consensus.params import ConsensusParams
from shared.consensus.subsidy import get_block_subsidy
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


class _IndexStub:
    def __init__(self, prev_hash: str):
        self.prev_hash = prev_hash

    def get_block(self, block_hash):
        if block_hash == self.prev_hash:
            return object()
        return None


def _coinbase(tag: bytes = b"\x02\x00", value: int = 0) -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=tag, sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(value=value, script_pubkey=b"\x51")]
    return tx


def _normal_spend() -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [TxIn(prev_tx_hash=b"\x11" * 32, prev_tx_index=0, script_sig=b"\x01\x01", sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(value=1, script_pubkey=b"\x51")]
    return tx


def _block(prev_hash_hex: str, txs) -> Block:
    root = merkle_root([tx.txid() for tx in txs]) or (b"\x00" * 32)
    hdr = BlockHeader(
        version=1,
        prev_block_hash=bytes.fromhex(prev_hash_hex),
        merkle_root=root,
        timestamp=1_700_000_000,
        bits=0x207FFFFF,
        nonce=0,
    )
    return Block(hdr, list(txs))


class TestCoinbaseSubsidyDedicated(unittest.TestCase):
    def test_coinbase_must_be_first_and_unique(self):
        params = ConsensusParams.regtest()
        validator = BlockValidator(params, utxo_store=type("U", (), {"get_utxo": lambda *_a, **_k: None})(), block_index=_IndexStub("22" * 32))
        txs = [_normal_spend(), _coinbase()]
        block = _block("22" * 32, txs)
        self.assertFalse(validator.validate_transactions(block, height=1))

        txs2 = [_coinbase(b"\x02\x01"), _coinbase(b"\x02\x02")]
        block2 = _block("22" * 32, txs2)
        self.assertFalse(validator.validate_transactions(block2, height=1))

    def test_coinbase_null_outpoint_required(self):
        params = ConsensusParams.regtest()
        validator = BlockValidator(params, utxo_store=type("U", (), {"get_utxo": lambda *_a, **_k: None})(), block_index=_IndexStub("22" * 32))
        bad_cb = Transaction(version=1)
        bad_cb.vin = [TxIn(prev_tx_hash=b"\x12" * 32, prev_tx_index=1, script_sig=b"\x02\x00", sequence=0xFFFFFFFF)]
        bad_cb.vout = [TxOut(value=1, script_pubkey=b"\x51")]
        self.assertFalse(validator.validate_transaction(bad_cb, height=1, is_coinbase=True))

    def test_coinbase_reward_bound_and_underclaim_allowed(self):
        params = ConsensusParams.regtest()
        subsidy = get_block_subsidy(1, params)
        prev_hash = "33" * 32
        utxo_store = type(
            "U",
            (),
            {"get_utxo": lambda _self, _txid, _idx: {"value": 10_000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": False}},
        )()
        validator = BlockValidator(params, utxo_store=utxo_store, block_index=_IndexStub(prev_hash))

        spend = Transaction(version=1)
        spend.vin = [TxIn(prev_tx_hash=b"\xaa" * 32, prev_tx_index=0, script_sig=b"\x01\x01", sequence=0xFFFFFFFF)]
        spend.vout = [TxOut(value=9_000, script_pubkey=b"\x51")]  # fee 1000

        over = _coinbase(value=subsidy + 1_001)
        block_over = _block(prev_hash, [over, spend])
        self.assertFalse(validator.validate_subsidy(block_over, 1))

        under = _coinbase(value=subsidy)  # less than subsidy+fees is allowed
        block_under = _block(prev_hash, [under, spend])
        self.assertTrue(validator.validate_subsidy(block_under, 1))

    def test_subsidy_halving_schedule_decreases_at_interval(self):
        params = ConsensusParams.regtest()
        h = int(params.subsidy_halving_interval)
        self.assertGreater(get_block_subsidy(0, params), get_block_subsidy(h, params))
        self.assertEqual(get_block_subsidy(h, params), get_block_subsidy(0, params) // 2)

    def test_connect_sets_coinbase_utxo_flag_and_height(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db = Database(Path(tmp.name), "regtest")
            db.connect()
            migrations = Migrations(db)
            register_standard_migrations(migrations)
            migrations.migrate()

            utxo = UTXOStore(db)
            index = BlockIndex(db)
            connect = ConnectBlock(utxo, index)

            cb = _coinbase(value=50)
            blk = _block("44" * 32, [cb])
            index.add_block(blk, height=1, chainwork=1, update_best=False)

            self.assertTrue(connect.connect(blk))
            row = utxo.get_utxo(cb.txid().hex(), 0)
            self.assertIsNotNone(row)
            self.assertEqual(int(row["height"]), 1)
            self.assertTrue(bool(row["is_coinbase"]))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_immature_then_mature_coinbase_spend_rules(self):
        params = ConsensusParams.regtest()
        prev_hash = "55" * 32
        maturity = int(getattr(params, "coinbase_maturity", 100))
        utxo_store = type(
            "U",
            (),
            {"get_utxo": lambda _self, _txid, _idx: {"value": 5_000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": True}},
        )()
        validator = BlockValidator(params, utxo_store=utxo_store, block_index=_IndexStub(prev_hash))
        tx = Transaction(version=1)
        tx.vin = [TxIn(prev_tx_hash=b"\xaa" * 32, prev_tx_index=0, script_sig=b"\x01\x01", sequence=0xFFFFFFFF)]
        tx.vout = [TxOut(value=1_000, script_pubkey=b"\x51")]
        with patch("node.chain.validation.verify_input_script", return_value=True):
            self.assertFalse(validator.validate_transaction(tx, height=max(1, maturity - 1), is_coinbase=False))
            self.assertTrue(validator.validate_transaction(tx, height=maturity + 1, is_coinbase=False))


if __name__ == "__main__":
    unittest.main()
