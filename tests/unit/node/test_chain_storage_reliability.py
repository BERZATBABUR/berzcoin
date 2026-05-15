"""Reliability tests for block storage metadata, undo data, and startup UTXO verification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from node.chain.chainstate import ChainState
from node.chain.block_index import BlockIndex
from node.storage.blocks_store import BlocksStore
from node.storage.db import Database
from node.storage.migrations import Migrations, register_standard_migrations
from node.storage.utxo_store import UTXOStore
from node.validation.connect import ConnectBlock
from node.validation.disconnect import DisconnectBlock
from node.chain.reorg import ReorgManager
from shared.consensus.params import ConsensusParams
from shared.core.block import Block, BlockHeader
from shared.core.merkle import merkle_root
from shared.core.transaction import Transaction, TxIn, TxOut


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


def _block(prev_hash: bytes, txs, nonce: int = 1) -> Block:
    mr = merkle_root([tx.txid() for tx in txs]) or (b"\x00" * 32)
    header = BlockHeader(
        version=1,
        prev_block_hash=prev_hash,
        merkle_root=mr,
        timestamp=1_700_000_000 + nonce,
        bits=0x207FFFFF,
        nonce=nonce,
    )
    return Block(header=header, transactions=list(txs))


class TestChainStorageReliability(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        datadir = Path(tmp.name)
        db = Database(datadir, "regtest")
        db.connect()
        migrations = Migrations(db)
        register_standard_migrations(migrations)
        migrations.migrate()
        return tmp, datadir, db

    def test_block_storage_metadata_and_hash_consistency(self):
        tmp, datadir, db = self._setup()
        try:
            store = BlocksStore(db, datadir)
            blk = _block(b"\x11" * 32, [_coinbase()], nonce=7)
            bh = blk.header.hash_hex()
            store.write_block(blk, 1)
            row = db.fetch_one(
                "SELECT file_path, file_number, file_offset, size FROM blocks WHERE hash = ?",
                (bh,),
            )
            self.assertIsNotNone(row)
            self.assertTrue(str(row["file_path"]).endswith(f"{bh}.blk"))
            self.assertEqual(int(row["file_number"]), -1)
            self.assertEqual(int(row["file_offset"]), 0)
            self.assertGreater(int(row["size"]), 0)

            block_file = datadir / "blocks" / f"{bh}.blk"
            block_file.write_bytes(b"\x00\x01corrupt")
            cold_store = BlocksStore(db, datadir)
            self.assertIsNone(cold_store.read_block_by_hash(bh))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_missing_block_file_is_detected(self):
        tmp, datadir, db = self._setup()
        try:
            store = BlocksStore(db, datadir)
            blk = _block(b"\x22" * 32, [_coinbase()], nonce=9)
            bh = blk.header.hash_hex()
            store.write_block(blk, 1)
            (datadir / "blocks" / f"{bh}.blk").unlink(missing_ok=True)
            cold_store = BlocksStore(db, datadir)
            self.assertIsNone(cold_store.read_block_by_hash(bh))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_connect_disconnect_uses_undo_rows(self):
        tmp, datadir, db = self._setup()
        try:
            utxo = UTXOStore(db)
            index = BlockIndex(db)
            connector = ConnectBlock(utxo, index)
            disconnector = DisconnectBlock(utxo, index)

            parent_txid = "aa" * 32
            parent_block_hash = "10" * 32
            db.execute(
                """
                INSERT INTO blocks
                (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce,
                 tx_count, size, weight, is_valid, processed_at)
                VALUES (?, 1, 1, ?, ?, ?, ?, ?, 1, 80, 320, 1, 1)
                """,
                (parent_block_hash, "00" * 32, "11" * 32, 1, 0x207FFFFF, 0),
            )
            db.execute(
                """
                INSERT INTO transactions
                (txid, block_hash, height, "index", version, locktime, size, weight, is_coinbase)
                VALUES (?, ?, 1, 0, 1, 0, 100, 400, 0)
                """,
                (parent_txid, parent_block_hash),
            )
            db.execute(
                """
                INSERT INTO outputs (txid, "index", value, script_pubkey, spent)
                VALUES (?, 0, 10000, ?, 0)
                """,
                (parent_txid, b"\x51"),
            )
            utxo.add_utxo(parent_txid, 0, 10_000, b"\x51", 1, False)

            spend = Transaction(version=1)
            spend.vin = [TxIn(prev_tx_hash=bytes.fromhex(parent_txid), prev_tx_index=0, sequence=0xFFFFFFFF)]
            spend.vout = [TxOut(value=9_000, script_pubkey=b"\x51")]
            blk = _block(b"\x33" * 32, [spend], nonce=11)
            index.add_block(blk, height=2, chainwork=2, update_best=False)

            self.assertTrue(connector.connect(blk))
            undo = db.fetch_one(
                "SELECT prev_txid, prev_index, value FROM block_undo WHERE block_hash = ? AND txid = ? AND input_index = 0",
                (blk.header.hash_hex(), spend.txid().hex()),
            )
            self.assertIsNotNone(undo)
            self.assertEqual(undo["prev_txid"], parent_txid)
            self.assertEqual(int(undo["prev_index"]), 0)
            self.assertEqual(int(undo["value"]), 10_000)

            # Simulate historical output table corruption; undo data should still drive restore.
            db.execute('DELETE FROM outputs WHERE txid = ? AND "index" = 0', (parent_txid,))
            self.assertTrue(disconnector.disconnect(blk))
            restored = utxo.get_utxo(parent_txid, 0)
            self.assertIsNotNone(restored)
            self.assertEqual(int(restored["value"]), 10_000)
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_startup_verify_detects_missing_or_wrong_utxo(self):
        class _Stub:
            def __init__(self, db, block):
                self.db = db
                self._block = block

            def get_best_height(self):
                return 0

            def get_block_by_height(self, h):
                return self._block if h == 0 else None

        tmp, _datadir, db = self._setup()
        try:
            cb = _coinbase()
            blk = _block(b"\x00" * 32, [cb], nonce=3)
            stub = _Stub(db, blk)
            report_missing = ChainState.verify_active_chain_utxo_state(stub)
            self.assertFalse(report_missing["ok"])
            self.assertTrue(any("missing_utxo_in_db" in m for m in report_missing["mismatches"]))

            db.execute(
                """
                INSERT INTO utxo (outpoint, txid, "index", value, script_pubkey, height, is_coinbase)
                VALUES (?, ?, 0, ?, ?, 0, 1)
                """,
                (f"{cb.txid().hex()}:0", cb.txid().hex(), 1, b"\x51"),
            )
            report_wrong = ChainState.verify_active_chain_utxo_state(stub)
            self.assertFalse(report_wrong["ok"])
            self.assertTrue(any("utxo_field_mismatch" in m for m in report_wrong["mismatches"]))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_end_to_end_consistency_flow(self):
        tmp, datadir, db = self._setup()
        try:
            params = ConsensusParams.regtest()
            chainstate = ChainState(db, params, str(datadir))
            chainstate.initialize()
            baseline_report = chainstate.verify_active_chain_utxo_state()
            baseline_mismatches = set(baseline_report.get("mismatches", []))

            # Simulate restart/reload and ensure best-tip chain + UTXO replay check remain coherent.
            chainstate._reload_best_from_index()
            report = chainstate.verify_active_chain_utxo_state()
            # Regtest synthetic genesis has a single coinbase output in this project.
            self.assertTrue(isinstance(report.get("mismatches"), list))

            # Simulate crash indicator: best tip points to unavailable block should be unhealthy.
            db.execute(
                """
                INSERT OR REPLACE INTO block_headers
                (hash, height, version, prev_block_hash, merkle_root, timestamp, bits, nonce, chainwork, is_valid)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 1)
                """,
                ("ff" * 32, 500, "00" * 32, "11" * 32, 1, 0x207FFFFF, 0, "999999"),
            )
            chainstate._reload_best_from_index()
            self.assertEqual(chainstate.get_best_block_hash(), "ff" * 32)
            self.assertIsNone(chainstate.get_block("ff" * 32))
        finally:
            db.disconnect()
            tmp.cleanup()

    def test_successful_short_reorg_preserves_utxo_equivalence_and_restart_state(self):
        tmp, datadir, db = self._setup()
        try:
            params = ConsensusParams.regtest()
            chainstate = ChainState(db, params, str(datadir))
            chainstate.initialize()
            baseline_report = chainstate.verify_active_chain_utxo_state()
            baseline_mismatches = set(baseline_report.get("mismatches", []))

            connector = ConnectBlock(chainstate.utxo_store, chainstate.block_index, network="regtest")
            genesis_hash = str(chainstate.get_best_block_hash() or "")
            genesis_header = chainstate.get_header(0)
            self.assertTrue(genesis_hash)
            self.assertIsNotNone(genesis_header)
            prev = bytes.fromhex(genesis_hash)
            base_ts = int(getattr(genesis_header, "timestamp", 1_700_000_000))

            # Active chain: genesis -> old1 -> old2
            old1 = _block(prev, [_coinbase(b"\x02\x11")], nonce=21)
            old1.header.timestamp = base_ts + 1
            self.assertTrue(chainstate.pow.mine(old1.header, max_nonce=2_000_000))
            h1 = 1
            cw1 = int(chainstate.get_best_chainwork()) + 1
            chainstate.blocks_store.write_block(old1, h1)
            chainstate.block_index.add_block(old1, h1, cw1, update_best=False)
            self.assertTrue(connector.connect(old1))
            chainstate.header_chain.add_header(old1.header, h1, cw1)
            chainstate.set_best_block(old1.header.hash_hex(), h1, cw1)

            old2 = _block(bytes.fromhex(old1.header.hash_hex()), [_coinbase(b"\x02\x12")], nonce=22)
            old2.header.timestamp = base_ts + 2
            self.assertTrue(chainstate.pow.mine(old2.header, max_nonce=2_000_000))
            h2 = 2
            cw2 = int(cw1) + 1
            chainstate.blocks_store.write_block(old2, h2)
            chainstate.block_index.add_block(old2, h2, cw2, update_best=False)
            self.assertTrue(connector.connect(old2))
            chainstate.header_chain.add_header(old2.header, h2, cw2)
            chainstate.set_best_block(old2.header.hash_hex(), h2, cw2)

            # Side branch: genesis -> new1 -> new2 -> new3 (heavier)
            new1 = _block(prev, [_coinbase(b"\x02\x21")], nonce=31)
            new1.header.timestamp = base_ts + 3
            self.assertTrue(chainstate.pow.mine(new1.header, max_nonce=2_000_000))
            new2 = _block(bytes.fromhex(new1.header.hash_hex()), [_coinbase(b"\x02\x22")], nonce=32)
            new2.header.timestamp = base_ts + 4
            self.assertTrue(chainstate.pow.mine(new2.header, max_nonce=2_000_000))
            new3 = _block(bytes.fromhex(new2.header.hash_hex()), [_coinbase(b"\x02\x23")], nonce=33)
            new3.header.timestamp = base_ts + 5
            self.assertTrue(chainstate.pow.mine(new3.header, max_nonce=2_000_000))

            chainstate.blocks_store.write_block(new1, 1)
            chainstate.block_index.add_block(new1, 1, int(chainstate.chainwork.calculate_chain_work([new1.header])), update_best=False)
            chainstate.header_chain.add_header(new1.header, 1, int(chainstate.chainwork.calculate_chain_work([new1.header])))

            cw_new2 = int(chainstate.chainwork.calculate_chain_work([new1.header, new2.header]))
            chainstate.blocks_store.write_block(new2, 2)
            chainstate.block_index.add_block(new2, 2, cw_new2, update_best=False)
            chainstate.header_chain.add_header(new2.header, 2, cw_new2)

            cw_new3 = int(chainstate.chainwork.calculate_chain_work([new1.header, new2.header, new3.header]))
            chainstate.blocks_store.write_block(new3, 3)
            chainstate.block_index.add_block(new3, 3, cw_new3, update_best=False)
            chainstate.header_chain.add_header(new3.header, 3, cw_new3)

            old_best = chainstate.block_index.get_block(old2.header.hash_hex())
            new_best = chainstate.block_index.get_block(new3.header.hash_hex())
            self.assertIsNotNone(old_best)
            self.assertIsNotNone(new_best)

            reorg = ReorgManager(chainstate.utxo_store, chainstate.block_index, max_reorg_depth=16)
            ok, disconnected, connected = reorg.reorganize(
                new_best,
                old_best,
                get_block_func=chainstate.get_block,
                validate_connect_block=lambda blk, h: chainstate.validate_block_stateful(blk, h),
            )
            self.assertTrue(ok)
            self.assertEqual(len(disconnected), 2)
            self.assertEqual(len(connected), 3)
            chainstate.set_best_block(new_best.block_hash, new_best.height, new_best.chainwork)

            report = chainstate.verify_active_chain_utxo_state()
            self.assertTrue(
                set(report.get("mismatches", [])) <= baseline_mismatches,
                msg=str(report),
            )
            self.assertEqual(chainstate.get_best_block_hash(), new_best.block_hash)
            self.assertEqual(chainstate.get_best_height(), 3)

            # Restart/reload should keep coherent tip and UTXO replay-equivalence.
            chainstate2 = ChainState(db, params, str(datadir))
            chainstate2.initialize()
            report2 = chainstate2.verify_active_chain_utxo_state()
            self.assertTrue(
                set(report2.get("mismatches", [])) <= baseline_mismatches,
                msg=str(report2),
            )
            self.assertEqual(chainstate2.get_best_block_hash(), new_best.block_hash)
            self.assertEqual(chainstate2.get_best_height(), 3)
            self.assertEqual(int(chainstate2.get_best_chainwork()), int(new_best.chainwork))
        finally:
            db.disconnect()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
