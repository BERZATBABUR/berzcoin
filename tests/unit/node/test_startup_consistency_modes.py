"""Startup consistency/reconciliation mode tests."""

import tempfile
import unittest
from pathlib import Path

from node.chain.chainstate import ChainState
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from shared.consensus.params import ConsensusParams
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


def _coinbase(tag: bytes = b"\x02\x01") -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=tag, sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(value=50_000, script_pubkey=b"\x51")]
    return tx


def _block(prev_hash: bytes, txs, nonce: int = 1) -> Block:
    mr = merkle_root([tx.txid() for tx in txs]) or (b"\x00" * 32)
    return Block(
        header=BlockHeader(
            version=1,
            prev_block_hash=prev_hash,
            merkle_root=mr,
            timestamp=1_700_000_000 + nonce,
            bits=0x207FFFFF,
            nonce=nonce,
        ),
        transactions=list(txs),
    )


class TestStartupConsistencyModes(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        datadir = Path(tmp.name)
        db = Database(datadir, "regtest")
        db.connect()
        mig = Migrations(db)
        register_standard_migrations(mig)
        mig.migrate()
        chain = ChainState(db, ConsensusParams.regtest(), str(datadir))
        chain.initialize()
        return tmp, datadir, db, chain

    def test_verify_mode_detects_raw_only_block(self):
        tmp, datadir, db, chain = self._setup()
        try:
            prev = bytes.fromhex(chain.get_best_block_hash())
            blk = _block(prev, [_coinbase(b"\x02\x07")], nonce=7)
            raw = datadir / "blocks" / f"{blk.header.hash_hex()}.blk"
            raw.write_bytes(blk.serialize())
            report = chain.run_startup_consistency("verify")
            self.assertFalse(report["ok"])
            self.assertTrue(report["checks"]["raw_blocks"]["raw_only"])
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_recovery_mode_reindexes_safe_raw_only_block(self):
        tmp, datadir, db, chain = self._setup()
        try:
            prev = bytes.fromhex(chain.get_best_block_hash())
            blk = _block(prev, [_coinbase(b"\x02\x08")], nonce=8)
            raw = datadir / "blocks" / f"{blk.header.hash_hex()}.blk"
            raw.write_bytes(blk.serialize())
            report = chain.run_startup_consistency("recovery")
            self.assertTrue(any(str(x).startswith("reindexed_raw_block:") for x in report.get("repairs", [])))
            self.assertIsNotNone(chain.block_index.get_block(blk.header.hash_hex()))
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
