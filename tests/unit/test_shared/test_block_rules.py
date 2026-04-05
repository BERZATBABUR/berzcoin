"""Regression tests for consensus block-level structural rules."""

import time
import unittest

from shared.consensus.buried_deployments import HARDFORK_TX_V2, SOFTFORK_BIP34_STRICT
from shared.consensus.params import ConsensusParams
from shared.consensus.pow import ProofOfWork
from shared.consensus.rules import ConsensusRules
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


def _coinbase(tag: bytes) -> Transaction:
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


def _mine_header_with_txs(txs):
    params = ConsensusParams.regtest()
    pow_check = ProofOfWork(params)
    txids = [tx.txid() for tx in txs]
    mr = merkle_root(txids) or (b"\x00" * 32)
    bits = pow_check.get_bits(params.pow_limit)
    header = BlockHeader(
        version=1,
        prev_block_hash=b"\x00" * 32,
        merkle_root=mr,
        timestamp=int(time.time()),
        bits=bits,
        nonce=0,
    )
    if not pow_check.mine(header, max_nonce=200_000):
        raise RuntimeError("failed to mine test header")
    return header


class TestBlockRules(unittest.TestCase):
    def test_rejects_empty_block(self) -> None:
        params = ConsensusParams.regtest()
        rules = ConsensusRules(params)
        header = _mine_header_with_txs([_coinbase(b"\x02\x00")])
        block = Block(header=header, transactions=[])
        with self.assertRaises(ValueError):
            rules.validate_block(block, prev_block=None, height=0)

    def test_rejects_second_coinbase_transaction(self) -> None:
        params = ConsensusParams.regtest()
        rules = ConsensusRules(params)
        txs = [_coinbase(b"\x02\x00"), _coinbase(b"\x02\x01")]
        header = _mine_header_with_txs(txs)
        block = Block(header=header, transactions=txs)
        with self.assertRaises(ValueError):
            rules.validate_block(block, prev_block=None, height=0)

    def test_subsidy_allows_coinbase_plus_fees_when_fee_lookup_available(self) -> None:
        params = ConsensusParams.regtest()
        subsidy = params.initial_subsidy

        prev_txid = bytes.fromhex("11" * 32)
        spend = Transaction(version=2)
        spend.vin = [
            TxIn(
                prev_tx_hash=prev_txid,
                prev_tx_index=0,
                script_sig=b"\x51",
                sequence=0xFFFFFFFF,
            )
        ]
        spend.vout = [TxOut(value=990, script_pubkey=b"\x51")]  # fee = 10

        cb = _coinbase(b"\x02\x00")
        cb.vout = [TxOut(value=subsidy + 10, script_pubkey=b"\x51")]
        txs = [cb, spend]
        header = _mine_header_with_txs(txs)
        block = Block(header=header, transactions=txs)

        def lookup(txid: str, index: int):
            if txid == prev_txid.hex() and index == 0:
                return 1000
            return None

        rules = ConsensusRules(params, output_value_lookup=lookup)
        self.assertTrue(rules.validate_block(block, prev_block=None, height=0))

    def test_softfork_strict_bip34_enforces_minimal_height_encoding(self) -> None:
        height = 200
        height_bytes = height.to_bytes((height.bit_length() + 7) // 8, "little")
        cb = _coinbase(b"\x03\xaa\xbb" + height_bytes)

        legacy_params = ConsensusParams.regtest()
        legacy_rules = ConsensusRules(legacy_params)
        self.assertTrue(legacy_rules.validate_coinbase_height(cb, height))

        strict_params = ConsensusParams.regtest()
        strict_params.custom_activation_heights = {SOFTFORK_BIP34_STRICT: 0}
        strict_rules = ConsensusRules(strict_params)
        with self.assertRaises(ValueError):
            strict_rules.validate_coinbase_height(cb, height)

    def test_hardfork_v2_tx_version_rejects_v1_after_activation(self) -> None:
        params = ConsensusParams.regtest()
        params.custom_activation_heights = {HARDFORK_TX_V2: 300}
        rules = ConsensusRules(params)

        tx = Transaction(version=1)
        tx.vin = [
            TxIn(
                prev_tx_hash=bytes.fromhex("11" * 32),
                prev_tx_index=0,
                script_sig=b"\x01\x01",
                sequence=0xFFFFFFFF,
            )
        ]
        tx.vout = [TxOut(value=1, script_pubkey=b"\x51")]

        # Pre-activation still allows version 1.
        self.assertTrue(rules.validate_transaction(tx, height=299))

        # Post-activation rejects version 1.
        with self.assertRaises(ValueError):
            rules.validate_transaction(tx, height=300)


if __name__ == "__main__":
    unittest.main()
