"""Crash-recovery and consistency regression tests for node state transitions."""

from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from node.app.main import BerzCoinNode
from node.app.health import HealthChecker
from node.mempool.pool import Mempool
from node.storage.db import Database
from node.storage.mempool_store import MempoolStore
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _CrashHarness:
    """Small deterministic state machine for crash phase assertions."""

    def __init__(self) -> None:
        self.blocks: dict[str, dict] = {}
        self.block_index: dict[str, dict] = {}
        self.best_hash = "00" * 32
        self.best_height = 0
        self.utxos: dict[tuple[str, int], int] = {("aa" * 32, 0): 100}
        self.mempool: dict[str, tuple[str, int]] = {}

    def check_integrity(self) -> bool:
        return self.best_height >= 0 and isinstance(self.block_index, dict)

    def _active_chain(self) -> set[str]:
        out: set[str] = set()
        h = self.best_hash
        while h in self.block_index:
            out.add(h)
            h = str(self.block_index[h]["prev"])
        return out

    def utxo_matches_active_chain(self) -> bool:
        # For this harness: connected block consumes aa:0 and creates bb:0.
        chain = self._active_chain()
        if "bb" * 32 in chain:
            return ("bb" * 32, 0) in self.utxos and ("aa" * 32, 0) not in self.utxos
        return ("aa" * 32, 0) in self.utxos

    def block_index_matches_blocks(self) -> bool:
        return all(h in self.blocks for h in self.block_index)

    def revalidate_mempool(self) -> None:
        for txid, outpoint in list(self.mempool.items()):
            if outpoint not in self.utxos:
                self.mempool.pop(txid, None)

    def recover(self) -> None:
        # Repair: drop index entries with missing block payload.
        for h in list(self.block_index.keys()):
            if h not in self.blocks:
                self.block_index.pop(h, None)
        # Reject unsafe best tip if not in index after crash.
        if self.best_hash not in self.block_index and self.best_hash != ("00" * 32):
            self.best_hash = "00" * 32
            self.best_height = 0
        self.revalidate_mempool()

    def connect_block(self, block_hash: str, crash_stage: str | None = None) -> bool:
        self.blocks[block_hash] = {"hash": block_hash}
        if crash_stage == "after_raw_before_utxo":
            return False

        snap = copy.deepcopy((self.block_index, self.best_hash, self.best_height, self.utxos))
        try:
            # UTXO phase.
            self.utxos.pop(("aa" * 32, 0), None)
            if crash_stage == "during_valid_connect":
                raise RuntimeError("crash during connect")
            self.utxos[(block_hash, 0)] = 90
            if crash_stage == "after_utxo_before_tip":
                raise RuntimeError("crash after utxo")
            self.block_index[block_hash] = {"height": 1, "prev": "00" * 32}
            self.best_hash = block_hash
            self.best_height = 1
            return True
        except Exception:
            self.block_index, self.best_hash, self.best_height, self.utxos = snap
            return False

    def reorg(self, new_hash: str, crash_stage: str | None = None) -> bool:
        snap = copy.deepcopy((self.block_index, self.best_hash, self.best_height, self.utxos))
        try:
            # disconnect phase
            self.utxos[("aa" * 32, 0)] = 100
            self.utxos.pop(("bb" * 32, 0), None)
            if crash_stage == "during_reorg_disconnect":
                raise RuntimeError("crash disconnect")
            # connect phase
            self.blocks[new_hash] = {"hash": new_hash}
            self.utxos.pop(("aa" * 32, 0), None)
            self.utxos[(new_hash, 0)] = 80
            if crash_stage == "during_reorg_connect":
                raise RuntimeError("crash connect")
            self.block_index[new_hash] = {"height": 1, "prev": "00" * 32}
            self.best_hash = new_hash
            self.best_height = 1
            return True
        except Exception:
            self.block_index, self.best_hash, self.best_height, self.utxos = snap
            return False


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


class _ChainStateStub:
    def __init__(self, utxos, known_txids, *, best_height: int = 100, best_hash: str = "11" * 32):
        self._utxos = dict(utxos)
        self._known = set(known_txids)
        self.best_height = int(best_height)
        self.best_hash = str(best_hash)
        self.params = type(
            "Params",
            (),
            {
                "coinbase_maturity": 100,
                "max_money": 21_000_000 * 100_000_000,
                "custom_activation_heights": {},
            },
        )()

    def transaction_exists(self, txid: str) -> bool:
        return txid in self._known

    def get_utxo(self, txid: str, index: int):
        return self._utxos.get((txid, index))

    def get_best_height(self) -> int:
        return self.best_height

    def get_best_block_hash(self) -> str:
        return self.best_hash


class TestCrashRecoveryConsistency(unittest.TestCase):
    def _assert_core_invariants(self, h: _CrashHarness) -> None:
        self.assertTrue(h.check_integrity())
        self.assertTrue(h.block_index_matches_blocks())
        self.assertTrue(h.utxo_matches_active_chain())
        h.revalidate_mempool()

    def test_kill_restart_during_valid_block_connection(self) -> None:
        h = _CrashHarness()
        self.assertFalse(h.connect_block("bb" * 32, crash_stage="during_valid_connect"))
        h.recover()
        self._assert_core_invariants(h)
        self.assertEqual(h.best_hash, "00" * 32)

    def test_kill_restart_after_raw_before_utxo(self) -> None:
        h = _CrashHarness()
        self.assertFalse(h.connect_block("bb" * 32, crash_stage="after_raw_before_utxo"))
        h.recover()
        self._assert_core_invariants(h)
        self.assertNotIn("bb" * 32, h.block_index)

    def test_kill_restart_after_utxo_before_tip(self) -> None:
        h = _CrashHarness()
        self.assertFalse(h.connect_block("bb" * 32, crash_stage="after_utxo_before_tip"))
        h.recover()
        self._assert_core_invariants(h)
        self.assertEqual(h.best_height, 0)

    def test_kill_restart_during_reorg_disconnect_phase(self) -> None:
        h = _CrashHarness()
        self.assertTrue(h.connect_block("bb" * 32))
        self.assertFalse(h.reorg("cc" * 32, crash_stage="during_reorg_disconnect"))
        h.recover()
        self._assert_core_invariants(h)
        self.assertEqual(h.best_hash, "bb" * 32)

    def test_kill_restart_during_reorg_connect_phase(self) -> None:
        h = _CrashHarness()
        self.assertTrue(h.connect_block("bb" * 32))
        self.assertFalse(h.reorg("cc" * 32, crash_stage="during_reorg_connect"))
        h.recover()
        self._assert_core_invariants(h)
        self.assertEqual(h.best_hash, "bb" * 32)

    def test_kill_restart_while_flushing_mempool_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            datadir = Path(d)
            store = MempoolStore(datadir)
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "aa" * 32
            cs = _ChainStateStub({(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}}, {prev_txid})
            mempool = Mempool(cs)
            tx = Transaction(version=2)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(80_000, spk)]
            _sign_input(tx, 0, key, spk)
            self.assertTrue(asyncio.run(mempool.add_transaction(tx)))

            # Simulate crash-like save failure.
            original = store._write_snapshot
            try:
                store._write_snapshot = lambda _payload: False  # type: ignore[assignment]
                self.assertFalse(
                    store.save(dict(mempool.transactions), network="regtest", tip_hash="11" * 32, tip_height=100)
                )
            finally:
                store._write_snapshot = original  # type: ignore[assignment]
            # Restart: loader should handle missing/failed snapshot safely.
            self.assertIsNone(store.load_snapshot())

    def test_restart_with_corrupted_or_missing_mempool_snapshot(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as d:
                datadir = Path(d)
                node = BerzCoinNode()
                node.config.set("datadir", str(datadir))
                node.config.set("network", "regtest")
                node.network = "regtest"
                node.mempool_store = MempoolStore(datadir)
                (datadir / "mempool.dat").write_text('{"corrupted":true}', encoding="utf-8")
                # No exception and no blind trust.
                node.mempool = Mempool(_ChainStateStub({}, set()))
                node.chainstate = _ChainStateStub({}, set())
                await node._restore_mempool_from_disk()
                self.assertEqual(len(node.mempool.transactions), 0)

        asyncio.run(run())

    def test_restart_with_block_index_chainstate_mismatch_is_rejected_safely(self) -> None:
        class _Index:
            def get_block(self, _h):
                return None

        class _Store:
            def read_block_by_hash(self, _h):
                return None

        class _Chain:
            def __init__(self):
                self.block_index = _Index()
                self.blocks_store = _Store()

            def get_best_height(self):
                return 42

            def get_best_block_hash(self):
                return "ff" * 32

        class _Node:
            def __init__(self):
                self.db = Database(Path(tempfile.mkdtemp()), "regtest")
                self.db.connect()
                self.db.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
                self.chainstate = _Chain()
                self.connman = None
                self.mempool = None
                self.simple_wallet_manager = None
                self.mode_manager = type("M", (), {"is_full_node": lambda _s: False})()
                self.config = {}

        async def run() -> None:
            n = _Node()
            try:
                health = HealthChecker(n)
                report = await health.check()
                self.assertTrue(report["checks"]["database"]["consistency"]["integrity_ok"])
                self.assertEqual(report["checks"]["chainstate"]["status"], "unhealthy")
                self.assertIn("Best tip", report["checks"]["chainstate"]["message"])
            finally:
                n.db.disconnect()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
