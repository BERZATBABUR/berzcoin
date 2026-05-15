"""Process-level crash injection tests for storage recovery checkpoints."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from node.storage.db import Database


def _run_enabled() -> bool:
    return str(os.getenv("BERZCOIN_RUN_REAL_NET_TESTS", "")).strip() in {"1", "true", "yes"}


SCRIPT = r"""
import os, sys
from pathlib import Path
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.chain.chainstate import ChainState
from shared.consensus.params import ConsensusParams
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from node.validation.connect import ConnectBlock
from node.chain.reorg import ReorgManager
from node.mempool.pool import Mempool
from node.storage.mempool_store import MempoolStore

datadir = Path(sys.argv[1])
scenario = sys.argv[2]
db = Database(datadir, "regtest")
db.connect()
m = Migrations(db)
register_standard_migrations(m)
m.migrate()

def coinbase(tag=b"\x02\x01"):
    tx = Transaction(version=1)
    tx.vin=[TxIn(prev_tx_hash=b"\x00"*32, prev_tx_index=0xFFFFFFFF, script_sig=tag, sequence=0xFFFFFFFF)]
    tx.vout=[TxOut(value=50_000, script_pubkey=b"\x51")]
    return tx

def mkblock(prev_hash: bytes, txs, nonce: int):
    mr = merkle_root([t.txid() for t in txs]) or (b"\x00"*32)
    return Block(header=BlockHeader(version=1, prev_block_hash=prev_hash, merkle_root=mr, timestamp=1700000000+nonce, bits=0x207fffff, nonce=nonce), transactions=list(txs))

cs = ChainState(db, ConsensusParams.regtest(), str(datadir))
cs.initialize()

if scenario == "block_write":
    # crash occurs while writing synthetic block file
    b = mkblock(bytes.fromhex(cs.get_best_block_hash()), [coinbase(b"\x02\x10")], 10)
    cs.blocks_store.write_block(b, 1)
elif scenario in {"block_connect", "utxo_update"}:
    g = cs.get_block_by_height(0)
    cb = g.transactions[0]
    spend = Transaction(version=1)
    spend.vin = [TxIn(prev_tx_hash=cb.txid(), prev_tx_index=0, script_sig=b"\x51", sequence=0xFFFFFFFF)]
    spend.vout = [TxOut(value=max(1, int(cb.vout[0].value)-1000), script_pubkey=b"\x51")]
    b = mkblock(bytes.fromhex(cs.get_best_block_hash()), [coinbase(b"\x02\x11"), spend], 11)
    h = 1
    pw = cs.chainwork.calculate_block_work_from_header(b.header)
    cw = int(cs.get_best_chainwork()) + int(pw)
    cs.blocks_store.write_block(b, h)
    cs.block_index.add_block(b, h, cw, update_best=False)
    cs.header_chain.add_header(b.header, h, cw)
    ConnectBlock(cs.utxo_store, cs.block_index).connect(b)
elif scenario in {"reorg_disconnect", "reorg_connect"}:
    prev = bytes.fromhex(cs.get_best_block_hash())
    old1 = mkblock(prev, [coinbase(b"\x02\x20")], 20)
    old2 = mkblock(bytes.fromhex(old1.header.hash_hex()), [coinbase(b"\x02\x21")], 21)
    for i, b in enumerate([old1, old2], start=1):
        cw = int(cs.get_best_chainwork()) + int(cs.chainwork.calculate_block_work_from_header(b.header))
        cs.blocks_store.write_block(b, i)
        cs.block_index.add_block(b, i, cw, update_best=False)
        cs.header_chain.add_header(b.header, i, cw)
        ConnectBlock(cs.utxo_store, cs.block_index).connect(b)
        cs.set_best_block(b.header.hash_hex(), i, cw)
    new1 = mkblock(prev, [coinbase(b"\x02\x30")], 30)
    new2 = mkblock(bytes.fromhex(new1.header.hash_hex()), [coinbase(b"\x02\x31")], 31)
    new3 = mkblock(bytes.fromhex(new2.header.hash_hex()), [coinbase(b"\x02\x32")], 32)
    parent_work = 0
    p = cs.block_index.get_block(cs.get_best_block_hash())
    if p: parent_work = int(p.chainwork)
    for i, b in enumerate([new1, new2, new3], start=1):
        parent = cs.block_index.get_block(b.header.prev_block_hash.hex())
        pw = int(parent.chainwork) if parent else parent_work
        cw = pw + int(cs.chainwork.calculate_block_work_from_header(b.header))
        cs.blocks_store.write_block(b, i)
        cs.block_index.add_block(b, i, cw, update_best=False)
        cs.header_chain.add_header(b.header, i, cw)
    old_best = cs.block_index.get_block(old2.header.hash_hex())
    new_best = cs.block_index.get_block(new3.header.hash_hex())
    ReorgManager(cs.utxo_store, cs.block_index, max_reorg_depth=32).reorganize(
        new_best,
        old_best,
        get_block_func=cs.get_block,
        validate_connect_block=lambda blk, h: cs.validate_block_stateful(blk, h),
    )
elif scenario == "mempool_flush":
    mp = Mempool(cs)
    store = MempoolStore(datadir)
    store.save(dict(mp.transactions), network="regtest", tip_hash=cs.get_best_block_hash(), tip_height=cs.get_best_height(), rules_fingerprint="x")
"""


@unittest.skipUnless(_run_enabled(), "set BERZCOIN_RUN_REAL_NET_TESTS=1 to run process crash tests")
class TestProcessCrashRecoveryStorage(unittest.TestCase):
    def _run_case(self, scenario: str, crash_point: str) -> None:
        with tempfile.TemporaryDirectory(prefix="berzcoin-crash-proc-") as d:
            datadir = Path(d)
            env = dict(os.environ)
            env["BERZCOIN_CRASH_POINT"] = crash_point
            proc = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(SCRIPT), str(datadir), scenario],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 137)
            db = Database(datadir, "regtest")
            db.connect()
            try:
                report = db.check_consistency(quick=True)
                self.assertTrue(report["integrity_ok"])
                self.assertTrue(report["foreign_keys_ok"])
            finally:
                db.disconnect()

    def test_crash_during_block_write(self):
        self._run_case("block_write", "during_block_write")

    def test_crash_during_block_connect(self):
        self._run_case("block_connect", "during_block_connect")

    def test_crash_during_utxo_update(self):
        self._run_case("utxo_update", "during_utxo_update")

    def test_crash_during_reorg_disconnect(self):
        self._run_case("reorg_disconnect", "during_reorg_disconnect")

    def test_crash_during_reorg_connect(self):
        self._run_case("reorg_connect", "during_reorg_connect")

    def test_crash_during_mempool_flush(self):
        self._run_case("mempool_flush", "during_mempool_flush")


if __name__ == "__main__":
    unittest.main()
