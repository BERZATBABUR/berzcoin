"""Wallet balance-bucket tests around immature/mature coinbase handling."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from node.wallet.simple_wallet import SimpleWalletManager


class _ChainStateStub:
    def __init__(self, best_height: int, maturity: int, by_addr):
        self._best_height = int(best_height)
        self.params = SimpleNamespace(coinbase_maturity=int(maturity))
        self._by_addr = dict(by_addr)

    def get_best_height(self) -> int:
        return self._best_height

    def get_utxos_for_address(self, address: str, _limit: int = 1000):
        return list(self._by_addr.get(address, []))


class TestWalletCoinbaseBalances(unittest.TestCase):
    def test_immature_coinbase_appears_in_immature_not_spendable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SimpleWalletManager(Path(tmp), network="regtest", wallet_passphrase="unit-test-passphrase")
            wallet = manager.create_wallet()
            manager.active_wallet = wallet

            cs = _ChainStateStub(
                best_height=100,
                maturity=100,
                by_addr={
                    wallet.address: [
                        {"txid": "aa" * 32, "index": 0, "value": 50, "height": 95, "is_coinbase": True},
                    ]
                },
            )
            b = manager.get_balance_breakdown(cs)
            self.assertEqual(b["total"], 50)
            self.assertEqual(b["immature_coinbase"], 50)
            self.assertEqual(b["spendable"], 0)

    def test_mature_coinbase_becomes_spendable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SimpleWalletManager(Path(tmp), network="regtest", wallet_passphrase="unit-test-passphrase")
            wallet = manager.create_wallet()
            manager.active_wallet = wallet

            cs = _ChainStateStub(
                best_height=200,
                maturity=100,
                by_addr={
                    wallet.address: [
                        {"txid": "bb" * 32, "index": 0, "value": 75, "height": 50, "is_coinbase": True},
                    ]
                },
            )
            b = manager.get_balance_breakdown(cs)
            self.assertEqual(b["total"], 75)
            self.assertEqual(b["immature_coinbase"], 0)
            self.assertEqual(b["spendable"], 75)


if __name__ == "__main__":
    unittest.main()
