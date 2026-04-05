"""Mempool persistence + restart correctness regression tests (P3)."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.app.main import BerzCoinNode
from node.mempool.pool import Mempool
from node.storage.mempool_store import MempoolStore
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


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


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


def _make_spend(prev_txid: str, spend_value: int, key: PrivateKey, spk: bytes) -> Transaction:
    tx = Transaction(version=2)
    tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
    tx.vout = [TxOut(spend_value, spk)]
    _sign_input(tx, 0, key, spk)
    return tx


def _build_node(datadir: Path, chainstate: _ChainStateStub) -> BerzCoinNode:
    node = BerzCoinNode()
    node.config.set("datadir", str(datadir))
    node.config.set("network", "regtest")
    node.config.set("persistmempool", True)
    node.network = "regtest"
    node.chainstate = chainstate
    node.mempool = Mempool(chainstate)
    node.mempool_store = MempoolStore(datadir)
    return node


class TestMempoolPersistenceRestart(unittest.TestCase):
    def test_normal_restart_restores_valid_pending_tx(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "aa" * 32
            utxo_value = 100_000

            with tempfile.TemporaryDirectory() as d:
                datadir = Path(d)
                cs1 = _ChainStateStub({(prev_txid, 0): {"value": utxo_value, "script_pubkey": spk}}, {prev_txid})
                n1 = _build_node(datadir, cs1)
                tx = _make_spend(prev_txid, 80_000, key, spk)
                self.assertTrue(await n1.mempool.add_transaction(tx))
                n1._flush_mempool_to_disk()

                cs2 = _ChainStateStub({(prev_txid, 0): {"value": utxo_value, "script_pubkey": spk}}, {prev_txid})
                n2 = _build_node(datadir, cs2)
                await n2._restore_mempool_from_disk()

                self.assertIn(tx.txid().hex(), n2.mempool.transactions)

        asyncio.run(run())

    def test_crash_like_restart_uses_backup_snapshot(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_a = "ab" * 32
            prev_b = "ac" * 32

            with tempfile.TemporaryDirectory() as d:
                datadir = Path(d)
                utxos = {
                    (prev_a, 0): {"value": 100_000, "script_pubkey": spk},
                    (prev_b, 0): {"value": 90_000, "script_pubkey": spk},
                }
                cs1 = _ChainStateStub(utxos, {prev_a, prev_b})
                n1 = _build_node(datadir, cs1)
                tx1 = _make_spend(prev_a, 70_000, key, spk)
                tx2 = _make_spend(prev_b, 60_000, key, spk)
                self.assertTrue(await n1.mempool.add_transaction(tx1))
                n1._flush_mempool_to_disk()
                self.assertTrue(await n1.mempool.add_transaction(tx2))
                n1._flush_mempool_to_disk()

                # Simulate crash/truncation of primary snapshot; loader should fall back to backup.
                (datadir / "mempool.dat").write_text('{"corrupted":true}', encoding="utf-8")

                cs2 = _ChainStateStub(utxos, {prev_a, prev_b})
                n2 = _build_node(datadir, cs2)
                await n2._restore_mempool_from_disk()

                self.assertIn(tx1.txid().hex(), n2.mempool.transactions)

        asyncio.run(run())

    def test_reorg_after_restart_drops_stale_inconsistent_tx(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "ad" * 32

            with tempfile.TemporaryDirectory() as d:
                datadir = Path(d)
                cs1 = _ChainStateStub({(prev_txid, 0): {"value": 120_000, "script_pubkey": spk}}, {prev_txid})
                n1 = _build_node(datadir, cs1)
                tx = _make_spend(prev_txid, 100_000, key, spk)
                self.assertTrue(await n1.mempool.add_transaction(tx))
                n1._flush_mempool_to_disk()

                # After restart/reorg this UTXO no longer exists; tx must not resurrect.
                cs2 = _ChainStateStub({}, {prev_txid})
                n2 = _build_node(datadir, cs2)
                await n2._restore_mempool_from_disk()

                self.assertNotIn(tx.txid().hex(), n2.mempool.transactions)
                self.assertGreaterEqual(n2.mempool.reject_reason_counts.get("missing_utxo", 0), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
