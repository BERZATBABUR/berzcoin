"""Consensus-validator regression tests for strict tx/block rules."""

import unittest
from unittest.mock import patch

from node.chain.validation import BlockValidator
from shared.consensus.params import ConsensusParams
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


class _UTXOStore:
    def __init__(self, utxos):
        self._utxos = dict(utxos)

    def get_utxo(self, txid, index):
        return self._utxos.get((txid, int(index)))


class _BlockIndex:
    def __init__(self, prev_hash, by_height=None):
        self.prev_hash = prev_hash
        self.by_height = by_height or {}

    def get_block(self, block_hash):
        if block_hash == self.prev_hash:
            return self.by_height.get(max(self.by_height.keys(), default=-1), object())
        return None

    def get_block_by_height(self, h):
        return self.by_height.get(h)


class _Entry:
    def __init__(self, header):
        self.header = header


def _coinbase(tag=b"\x02\x00"):
    tx = Transaction(version=1)
    tx.vin = [
        TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=tag,
            sequence=0xFFFFFFFF,
        )
    ]
    tx.vout = [TxOut(value=0, script_pubkey=b"")]
    return tx


def _spend(prev_txid_hex: str, prev_index: int = 0):
    tx = Transaction(version=1)
    tx.vin = [
        TxIn(
            prev_tx_hash=bytes.fromhex(prev_txid_hex),
            prev_tx_index=prev_index,
            script_sig=b"\x01\x01",
            sequence=0xFFFFFFFE,
        )
    ]
    tx.vout = [TxOut(value=1000, script_pubkey=b"\x51")]
    return tx


class TestBlockValidatorConsensus(unittest.TestCase):
    def test_rejects_block_level_double_spend(self):
        params = ConsensusParams.regtest()
        prev_hash = "11" * 32
        utxo_store = _UTXOStore({("aa" * 32, 0): {"value": 5000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": False}})
        block_index = _BlockIndex(prev_hash)
        validator = BlockValidator(params, utxo_store, block_index)

        cb = _coinbase()
        t1 = _spend("aa" * 32, 0)
        t2 = _spend("aa" * 32, 0)  # same outpoint in same block
        txs = [cb, t1, t2]
        header = BlockHeader(
            version=1,
            prev_block_hash=bytes.fromhex(prev_hash),
            merkle_root=merkle_root([tx.txid() for tx in txs]) or (b"\x00" * 32),
            timestamp=1_700_000_000,
            bits=0x207FFFFF,
            nonce=0,
        )
        block = Block(header=header, transactions=txs)

        with patch("node.chain.validation.verify_input_script", return_value=True):
            self.assertFalse(validator.validate_transactions(block, height=2))

    def test_rejects_oversized_transaction(self):
        params = ConsensusParams.regtest()
        params.max_block_size = 200
        utxo_store = _UTXOStore({})
        block_index = _BlockIndex("11" * 32)
        validator = BlockValidator(params, utxo_store, block_index)

        tx = Transaction(version=1)
        tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x02\x00", sequence=0xFFFFFFFF)]
        tx.vout = [TxOut(value=0, script_pubkey=b"\x6a" + b"x" * 1000)]
        self.assertFalse(validator.validate_transaction(tx, height=0, is_coinbase=True))

    def test_rejects_coinbase_overpay_above_subsidy_plus_fees(self):
        params = ConsensusParams.regtest()
        prev_hash = "11" * 32
        subsidy = params.initial_subsidy
        utxo_store = _UTXOStore({
            ("aa" * 32, 0): {"value": 10_000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": False}
        })
        validator = BlockValidator(params, utxo_store, _BlockIndex(prev_hash))

        cb = _coinbase()
        # Real fee for spend tx is 1_000 (10_000 in, 9_000 out).
        cb.vout = [TxOut(value=subsidy + 1_001, script_pubkey=b"\x51")]
        spend = _spend("aa" * 32, 0)
        spend.vout = [TxOut(value=9_000, script_pubkey=b"\x51")]
        block = Block(
            header=BlockHeader(
                version=1,
                prev_block_hash=bytes.fromhex(prev_hash),
                merkle_root=merkle_root([cb.txid(), spend.txid()]) or (b"\x00" * 32),
                timestamp=1_700_000_000,
                bits=0x207FFFFF,
                nonce=0,
            ),
            transactions=[cb, spend],
        )
        self.assertFalse(validator.validate_subsidy(block, height=1))

    def test_accepts_coinbase_exact_subsidy_plus_fees(self):
        params = ConsensusParams.regtest()
        prev_hash = "11" * 32
        subsidy = params.initial_subsidy
        utxo_store = _UTXOStore({
            ("aa" * 32, 0): {"value": 10_000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": False}
        })
        validator = BlockValidator(params, utxo_store, _BlockIndex(prev_hash))

        cb = _coinbase()
        spend = _spend("aa" * 32, 0)
        spend.vout = [TxOut(value=9_000, script_pubkey=b"\x51")]  # fee=1_000
        cb.vout = [TxOut(value=subsidy + 1_000, script_pubkey=b"\x51")]
        block = Block(
            header=BlockHeader(
                version=1,
                prev_block_hash=bytes.fromhex(prev_hash),
                merkle_root=merkle_root([cb.txid(), spend.txid()]) or (b"\x00" * 32),
                timestamp=1_700_000_000,
                bits=0x207FFFFF,
                nonce=0,
            ),
            transactions=[cb, spend],
        )
        self.assertTrue(validator.validate_subsidy(block, height=1))

    def test_coinbase_maturity_uses_validator_setting(self):
        params = ConsensusParams.regtest()
        setattr(params, "coinbase_maturity", 120)
        utxo_store = _UTXOStore({
            ("aa" * 32, 0): {"value": 5_000, "script_pubkey": b"\x51", "height": 1, "is_coinbase": True}
        })
        validator = BlockValidator(params, utxo_store, _BlockIndex("11" * 32))
        tx = _spend("aa" * 32, 0)
        with patch("node.chain.validation.verify_input_script", return_value=True):
            self.assertFalse(validator.validate_transaction(tx, height=100, is_coinbase=False))
            self.assertTrue(validator.validate_transaction(tx, height=121, is_coinbase=False))

    def test_rejects_header_with_unexpected_bits_at_retarget_height(self):
        params = ConsensusParams.regtest()
        params.pow_target_spacing = 60
        params.pow_target_timespan = 600  # interval=10
        params.pow_no_retargeting = False
        prev_hash = "11" * 32

        # Build prior 10-header window (heights 0..9).
        by_height = {}
        parent_hash_bytes = bytes.fromhex("00" * 32)
        old_bits = 0x207FFFFF
        for h in range(10):
            hdr = BlockHeader(
                version=1,
                prev_block_hash=parent_hash_bytes,
                merkle_root=b"\x22" * 32,
                timestamp=1_700_000_000 + (h * 60),
                bits=old_bits,
                nonce=0,
            )
            by_height[h] = _Entry(hdr)
            parent_hash_bytes = bytes.fromhex(hdr.hash_hex())
            if h == 9:
                prev_hash = hdr.hash_hex()

        validator = BlockValidator(params, _UTXOStore({}), _BlockIndex(prev_hash, by_height=by_height))
        # Isolate expected-bits check from PoW result.
        validator.pow.validate = lambda _h: True  # type: ignore[assignment]

        # Height 10 is a retarget boundary; setting old bits should fail if expected changes.
        candidate = BlockHeader(
            version=1,
            prev_block_hash=bytes.fromhex(prev_hash),
            merkle_root=b"\x33" * 32,
            timestamp=1_700_000_000 + (10 * 60),
            bits=old_bits,
            nonce=0,
        )
        expected = validator._expected_bits_for_height(10, by_height[9])
        self.assertIsNotNone(expected)
        if int(expected) == int(old_bits):
            # Force mismatch if compact conversion happens to round equal.
            candidate.bits = old_bits - 1
        self.assertFalse(validator.validate_header(candidate, height=10))


if __name__ == "__main__":
    unittest.main()
