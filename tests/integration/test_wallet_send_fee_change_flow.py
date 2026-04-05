"""Regression: private-key send flow must sign inputs and keep deterministic change."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from node.rpc.handlers.wallet import WalletHandlers
from node.wallet.simple_wallet import SimpleWalletManager
from node.wallet.core.tx_builder import TransactionBuilder
from shared.crypto.address import public_key_to_address
from shared.crypto.keys import PrivateKey


class _Config:
    def __init__(self, datadir: Path):
        self._datadir = datadir

    def get_datadir(self) -> Path:
        return self._datadir

    @staticmethod
    def get(key, default=None):
        if key == "network":
            return "regtest"
        return default


class _ChainState:
    def __init__(self, sender_address: str, utxo_value: int = 200_000):
        self.sender_address = sender_address
        self._spk = TransactionBuilder("regtest")._create_script_pubkey(sender_address)
        self._utxo_value = int(utxo_value)

    @staticmethod
    def get_balance(_address: str) -> int:
        return 0

    def get_utxos_for_address(self, address: str, _limit: int = 1000):
        if address != self.sender_address:
            return []
        return [
            {
                "txid": "11" * 32,
                "index": 0,
                "value": self._utxo_value,
                "height": 1,
            }
        ]

    def get_utxo(self, txid: str, index: int):
        if txid == ("11" * 32) and index == 0:
            return {
                "txid": txid,
                "index": index,
                "value": self._utxo_value,
                "script_pubkey": self._spk,
                "height": 1,
                "is_coinbase": False,
            }
        return None


class _Mempool:
    def __init__(self):
        self.last_tx = None

    async def add_transaction(self, tx):
        self.last_tx = tx
        return True


class _Node:
    def __init__(self, datadir: Path, sender_address: str, utxo_value: int = 200_000):
        self.config = _Config(datadir)
        self.chainstate = _ChainState(sender_address, utxo_value=utxo_value)
        self.mempool = _Mempool()
        self.simple_wallet_manager = None


class TestWalletSendFlow(unittest.TestCase):
    def test_send_adds_change_and_signatures(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                manager = SimpleWalletManager(Path(tmp))
                wallet = manager.create_wallet()
                manager.activate_wallet(wallet.private_key_hex)

                node = _Node(Path(tmp), wallet.address)
                node.simple_wallet_manager = manager
                handlers = WalletHandlers(node)

                recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")
                txid = await handlers.send_to_address(recipient, 0.001)
                self.assertTrue(txid)

                tx = node.mempool.last_tx
                self.assertIsNotNone(tx)
                self.assertEqual(len(tx.vin), 1)
                self.assertEqual(len(tx.vout), 2)

                # 1 input: fee estimate is 10 + 150 + 34 = 194 sat.
                sent = 100_000
                total_out = sum(o.value for o in tx.vout)
                self.assertEqual(200_000 - total_out, 194)
                self.assertEqual(tx.vout[0].value, sent)

                for txin in tx.vin:
                    self.assertTrue(txin.script_sig)
                    self.assertGreater(len(txin.script_sig), 35)

        asyncio.run(run())

    def test_send_rejects_address_network_mismatch(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                manager = SimpleWalletManager(Path(tmp))
                wallet = manager.create_wallet()
                manager.activate_wallet(wallet.private_key_hex)

                node = _Node(Path(tmp), wallet.address)
                node.simple_wallet_manager = manager
                handlers = WalletHandlers(node)

                # mainnet destination while wallet/node are regtest.
                bad_recipient = public_key_to_address(PrivateKey().public_key(), network="mainnet")
                with self.assertRaises(ValueError):
                    await handlers.send_to_address(bad_recipient, 0.001)

        asyncio.run(run())

    def test_send_absorbs_dust_change_into_fee(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                manager = SimpleWalletManager(Path(tmp))
                wallet = manager.create_wallet()
                manager.activate_wallet(wallet.private_key_hex)

                # 100000 send + 194 baseline fee => 406 sat pre-change (dust) with 100600 input.
                node = _Node(Path(tmp), wallet.address, utxo_value=100_600)
                node.simple_wallet_manager = manager
                handlers = WalletHandlers(node)

                recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")
                txid = await handlers.send_to_address(recipient, 0.001)
                self.assertTrue(txid)
                tx = node.mempool.last_tx
                self.assertIsNotNone(tx)
                # No dust change output should be created.
                self.assertEqual(len(tx.vout), 1)
                self.assertEqual(tx.vout[0].value, 100_000)
                # Entire remainder goes to fee.
                self.assertEqual(100_600 - sum(o.value for o in tx.vout), 600)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
