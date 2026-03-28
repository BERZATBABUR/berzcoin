"""Large negative consensus corpus (data-driven)."""

import json
import time
import unittest
from pathlib import Path

from shared.consensus.params import ConsensusParams
from shared.consensus.pow import ProofOfWork
from shared.consensus.rules import ConsensusRules
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut


def _coinbase(height: int = 0, value: int = 50_000) -> Transaction:
    script = b"\x02\x00\x00"
    tx = Transaction(version=1)
    tx.vin = [
        TxIn(
            prev_tx_hash=b"\x00" * 32,
            prev_tx_index=0xFFFFFFFF,
            script_sig=script,
            sequence=0xFFFFFFFF,
        )
    ]
    tx.vout = [TxOut(value=value, script_pubkey=b"\x51")]
    return tx


def _normal_tx() -> Transaction:
    tx = Transaction(version=2)
    tx.vin = [TxIn(prev_tx_hash=b"\x01" * 32, prev_tx_index=0, script_sig=b"\x51")]
    tx.vout = [TxOut(value=1_000, script_pubkey=b"\x51")]
    return tx


def _valid_block(params: ConsensusParams, height: int = 0) -> Block:
    cb = _coinbase(height=height, value=100_000)
    header = BlockHeader(
        version=1,
        prev_block_hash=b"\x00" * 32,
        merkle_root=b"\x00" * 32,
        timestamp=int(time.time()),
        bits=ProofOfWork(params).get_bits(params.pow_limit),
        nonce=0,
    )
    blk = Block(header=header, transactions=[cb])
    blk.header.merkle_root = blk.calculate_merkle_root()
    ok = ProofOfWork(params).mine(blk.header, max_nonce=300_000)
    if not ok:
        raise RuntimeError("failed to mine baseline corpus block")
    return blk


class TestConsensusNegativeCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        corpus_path = Path(__file__).with_name("negative_corpus.json")
        with open(corpus_path, "r", encoding="utf-8") as f:
            cls.corpus = json.load(f)

    def setUp(self) -> None:
        self.params = ConsensusParams.regtest()
        self.rules = ConsensusRules(self.params)

    def _mutate_tx(self, name: str) -> Transaction:
        tx = _normal_tx()
        max_supply = 21_000_000 * 100_000_000

        if name == "empty_inputs":
            tx.vin = []
        elif name == "empty_outputs":
            tx.vout = []
        elif name in ("negative_output", "negative_output_large"):
            tx.vout = [TxOut(value=-1 if name == "negative_output" else -500_000_000, script_pubkey=b"\x51")]
        elif name in ("total_out_exceeds_supply", "total_out_boundary_plus1"):
            tx.vout = [TxOut(value=max_supply + 1, script_pubkey=b"\x51")]
        elif name == "coinbase_two_inputs":
            tx.vin = [
                TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x02\x00\x00"),
                TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x02\x00\x00"),
            ]
        elif name in ("coinbase_script_short", "coinbase_script_len_1"):
            tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x01")]
        elif name in ("coinbase_script_long", "coinbase_script_len_101"):
            tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x00" * 101)]
        elif name.startswith("noncoinbase_zero_prevhash"):
            tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=1, script_sig=b"\x51")]
            if name == "noncoinbase_zero_prevhash_multiin":
                tx.vin.append(TxIn(prev_tx_hash=b"\x02" * 32, prev_tx_index=0, script_sig=b"\x51"))
        else:
            raise ValueError(f"unknown tx mutation: {name}")

        return tx

    def _mutate_block(self, name: str) -> Block:
        blk = _valid_block(self.params, height=0)

        if name == "bad_header_version":
            blk.header.version = 0x30000000
        elif name == "future_timestamp":
            blk.header.timestamp = int(time.time()) + 60 * 60 * 4
        elif name == "bad_pow":
            blk.header.bits = 0x1D00FFFF
            blk.header.nonce = 0
        elif name == "bad_merkle_root":
            blk.header.merkle_root = b"\x11" * 32
        elif name == "no_coinbase_first":
            blk.transactions[0] = _normal_tx()
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        elif name == "oversized_block":
            blk.transactions[0].vout[0] = TxOut(value=100_000, script_pubkey=b"\x51" * (self.params.max_block_size + 10))
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        elif name == "overweight_block":
            blk.transactions[0].vout[0] = TxOut(value=100_000, script_pubkey=b"\x51" * (self.params.max_block_size + 10))
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        elif name == "too_many_sigops":
            blk.transactions[0].vout[0] = TxOut(value=100_000, script_pubkey=bytes([0xAC]) * (self.params.max_block_sigops + 1))
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        elif name == "invalid_subsidy":
            blk.transactions[0].vout[0] = TxOut(value=self.params.initial_subsidy + 1, script_pubkey=b"\x51")
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        elif name == "second_coinbase":
            blk.transactions.append(_coinbase(height=0, value=1))
            blk.header.merkle_root = blk.calculate_merkle_root()
            ProofOfWork(self.params).mine(blk.header, max_nonce=300_000)
        else:
            raise ValueError(f"unknown block mutation: {name}")

        return blk

    def test_negative_consensus_corpus(self) -> None:
        failures = []
        for case in self.corpus:
            cid = case["id"]
            target = case["target"]
            mutation = case["mutation"]
            try:
                if target == "tx":
                    tx = self._mutate_tx(mutation)
                    self.rules.validate_transaction(tx, height=0)
                    failures.append(f"{cid}: expected validate_transaction to fail")
                elif target == "block":
                    blk = self._mutate_block(mutation)
                    self.rules.validate_block(blk, prev_block=None, height=0)
                    failures.append(f"{cid}: expected validate_block to fail")
                else:
                    failures.append(f"{cid}: invalid target {target}")
            except Exception:
                continue

        if failures:
            self.fail("\n".join(failures))


if __name__ == "__main__":
    unittest.main()
