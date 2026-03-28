"""Unit tests for mempool cleanup/revalidation after block connection."""

import asyncio
import unittest

from node.mempool.pool import Mempool
from shared.core.hashes import hash160
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.signatures import sign_message_hash
from shared.script.sigchecks import SIGHASH_ALL, calculate_legacy_sighash


class _ChainStateStub:
    def __init__(self, utxos, known_txids):
        self._utxos = dict(utxos)
        self._known = set(known_txids)
        self.params = type("Params", (), {"coinbase_maturity": 100})()

    def transaction_exists(self, txid: str) -> bool:
        return txid in self._known

    def get_utxo(self, txid: str, index: int):
        return self._utxos.get((txid, index))

    def get_best_height(self) -> int:
        return 100


def _p2pkh_script(pubkey_hash: bytes) -> bytes:
    return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"


def _sign_input(tx: Transaction, i: int, key: PrivateKey, script_pubkey: bytes) -> None:
    pub = key.public_key().to_bytes()
    sighash = calculate_legacy_sighash(tx, i, SIGHASH_ALL, script_pubkey)
    sig = sign_message_hash(key, sighash) + bytes([SIGHASH_ALL])
    tx.vin[i].script_sig = bytes([len(sig)]) + sig + bytes([len(pub)]) + pub


class _BlockStub:
    def __init__(self, txs):
        self.transactions = txs


class TestMempoolBlockCleanup(unittest.TestCase):
    def test_rejects_immature_coinbase_spend(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            prev_txid = "10" * 32
            spk = _p2pkh_script(hash160(pub))
            cs = _ChainStateStub(
                {
                    (prev_txid, 0): {
                        "value": 100_000,
                        "script_pubkey": spk,
                        "height": 95,  # best=100 => only 6 confirmations for next block
                        "is_coinbase": True,
                    }
                },
                {prev_txid},
            )
            mempool = Mempool(cs)

            tx = Transaction(version=2)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(90_000, spk)]
            _sign_input(tx, 0, key, spk)

            self.assertFalse(await mempool.add_transaction(tx))
            self.assertEqual(mempool.last_reject_reason, "coinbase_not_mature")

        asyncio.run(run())

    def test_confirmed_tx_removed_on_connected_block(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            prev_txid = "11" * 32
            spk = _p2pkh_script(hash160(pub))
            cs = _ChainStateStub({(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}}, {prev_txid})
            mempool = Mempool(cs)

            tx = Transaction(version=2)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(90_000, spk)]
            _sign_input(tx, 0, key, spk)
            self.assertTrue(await mempool.add_transaction(tx))
            self.assertIn(tx.txid().hex(), mempool.transactions)

            removed = await mempool.handle_connected_block(_BlockStub([tx]))
            self.assertIn(tx.txid().hex(), removed)
            self.assertNotIn(tx.txid().hex(), mempool.transactions)

        asyncio.run(run())

    def test_revalidate_removes_tx_when_input_becomes_spent_on_chain(self) -> None:
        async def run() -> None:
            key = PrivateKey()
            pub = key.public_key().to_bytes()
            prev_txid = "22" * 32
            spk = _p2pkh_script(hash160(pub))
            cs = _ChainStateStub({(prev_txid, 0): {"value": 100_000, "script_pubkey": spk}}, {prev_txid})
            mempool = Mempool(cs)

            tx = Transaction(version=2)
            tx.vin = [TxIn(prev_tx_hash=bytes.fromhex(prev_txid), prev_tx_index=0, sequence=0xFFFFFFFD)]
            tx.vout = [TxOut(90_000, spk)]
            _sign_input(tx, 0, key, spk)
            self.assertTrue(await mempool.add_transaction(tx))
            self.assertIn(tx.txid().hex(), mempool.transactions)

            # Simulate chain update spending this UTXO in a competing block.
            cs._utxos.pop((prev_txid, 0), None)
            removed = await mempool.handle_connected_block(_BlockStub([]))
            self.assertIn(tx.txid().hex(), removed)
            self.assertNotIn(tx.txid().hex(), mempool.transactions)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
