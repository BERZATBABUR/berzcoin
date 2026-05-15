"""Reorg mempool re-add pipeline regression tests."""

import asyncio
import unittest

from node.app.main import BerzCoinNode
from node.mempool.pool import Mempool
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _ChainStateStub:
    def __init__(self, utxos, known_txids=None, *, best_height: int = 100):
        self._utxos = dict(utxos)
        self._known = set(known_txids or set())
        self.best_height = int(best_height)
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


class _Header:
    def hash_hex(self) -> str:
        return "00" * 32


class _BlockStub:
    def __init__(self, txs):
        self.transactions = list(txs)
        self.header = _Header()


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


def _make_coinbase() -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=b"\x02\x00", sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(1, b"\x51")]
    return tx


class TestMempoolReorgReadd(unittest.TestCase):
    def test_disconnected_non_conflicting_tx_returns_to_mempool(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "11" * 32
            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                known_txids={prev_txid},
            )
            node = BerzCoinNode()
            node.chainstate = chainstate
            node.mempool = Mempool(chainstate)

            tx = _make_spend(prev_txid, 90_000, key, spk)
            stats = await node._reconcile_mempool_after_reorg([_BlockStub([tx])], [])
            self.assertEqual(stats.get("readded"), 1)
            self.assertIn(tx.txid().hex(), node.mempool.transactions)

        asyncio.run(run())

    def test_disconnected_tx_conflicting_with_new_chain_is_dropped(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "22" * 32
            tx = _make_spend(prev_txid, 90_000, key, spk)

            chainstate = _ChainStateStub(
                {(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}},
                known_txids={tx.txid().hex()},  # already confirmed on new chain
            )
            node = BerzCoinNode()
            node.chainstate = chainstate
            node.mempool = Mempool(chainstate)

            stats = await node._reconcile_mempool_after_reorg([_BlockStub([tx])], [])
            self.assertEqual(stats.get("readded"), 0)
            dropped = stats.get("dropped", {})
            self.assertEqual(int(dropped.get("already_in_chain", 0)), 1)
            self.assertNotIn(tx.txid().hex(), node.mempool.transactions)

        asyncio.run(run())

    def test_coinbase_from_disconnected_block_is_not_added(self) -> None:
        async def run() -> None:
            chainstate = _ChainStateStub({})
            node = BerzCoinNode()
            node.chainstate = chainstate
            node.mempool = Mempool(chainstate)
            cb = _make_coinbase()

            stats = await node._reconcile_mempool_after_reorg([_BlockStub([cb])], [])
            self.assertEqual(stats.get("candidates"), 0)
            self.assertEqual(stats.get("readded"), 0)
            self.assertEqual(len(node.mempool.transactions), 0)

        asyncio.run(run())

    def test_disconnected_tx_missing_input_after_reorg_is_not_added(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_txid = "33" * 32
            tx = _make_spend(prev_txid, 90_000, key, spk)
            chainstate = _ChainStateStub({})  # missing required input
            node = BerzCoinNode()
            node.chainstate = chainstate
            node.mempool = Mempool(chainstate)

            stats = await node._reconcile_mempool_after_reorg([_BlockStub([tx])], [])
            self.assertEqual(stats.get("readded"), 0)
            self.assertNotIn(tx.txid().hex(), node.mempool.transactions)
            self.assertGreaterEqual(int(stats.get("dropped", {}).get("missing_utxo", 0)), 1)

        asyncio.run(run())

    def test_existing_mempool_transactions_are_revalidated_or_evicted(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            spk = _p2pkh_script(hash160(key.public_key().to_bytes()))
            prev_old = "44" * 32
            prev_readd = "55" * 32
            chainstate = _ChainStateStub(
                {
                    # old input no longer exists on new chain -> existing mempool tx must be evicted
                    (prev_readd, 0): {"value": 100_000, "script_pubkey": spk},
                },
                known_txids={prev_old, prev_readd},
            )
            node = BerzCoinNode()
            node.chainstate = chainstate
            node.mempool = Mempool(chainstate)

            # Add existing mempool tx when old utxo still present.
            chainstate._utxos[(prev_old, 0)] = {"value": 100_000, "script_pubkey": spk}
            existing = _make_spend(prev_old, 90_000, key, spk)
            self.assertTrue(await node.mempool.add_transaction(existing))
            # Reorg switched UTXO set: old input disappears.
            chainstate._utxos.pop((prev_old, 0), None)

            readd = _make_spend(prev_readd, 90_000, key, spk)
            await node._reconcile_mempool_after_reorg([_BlockStub([readd])], [_BlockStub([])])
            self.assertNotIn(existing.txid().hex(), node.mempool.transactions)
            self.assertIn(readd.txid().hex(), node.mempool.transactions)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
