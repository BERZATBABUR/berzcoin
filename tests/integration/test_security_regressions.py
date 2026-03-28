"""Regression tests for security and wallet-path fixes."""

import asyncio
import shutil
import tempfile
import unittest

from node.app.main import BerzCoinNode
from node.app.modes import ModeManager
from node.chain.validation import BlockValidator
from node.rpc.handlers.wallet import WalletHandlers
from node.wallet.core.tx_builder import TransactionBuilder
from node.wallet.simple_wallet import SimpleWallet, SimpleWalletManager
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.keys import PrivateKey
from shared.crypto.address import public_key_to_address


class TestSecurityRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.node = BerzCoinNode()
        self.node.config.set("datadir", self.temp_dir)
        self.node.config.set("network", "regtest")
        self.node.config.set("port", 18444)
        self.node.config.set("rpcport", 18443)
        self.node.config.set("rpcbind", "127.0.0.1")
        self.node.config.set("wallet", "test_wallet")
        self.node.config.set("wallet_private_key", "")
        self.node.mode_manager = ModeManager(self.node.config)
        self.node.network = self.node.config.get("network", "regtest")

    def tearDown(self) -> None:
        if self.node.db:
            self.node.db.disconnect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mempool_rejects_invalid_signature(self) -> None:
        async def run_test() -> None:
            self.assertTrue(await self.node.initialize())
            self.assertIsNotNone(self.node.mempool)
            self.assertIsNotNone(self.node.utxo_store)

            source_priv = PrivateKey()
            source_addr = public_key_to_address(source_priv.public_key(), network="regtest")
            source_script = TransactionBuilder("regtest")._create_script_pubkey(source_addr)

            prev_txid = "11" * 32
            self.node.utxo_store.add_utxo(
                txid=prev_txid,
                index=0,
                value=120000,
                script_pubkey=source_script,
                height=1,
                is_coinbase=False,
            )

            dest_addr = public_key_to_address(PrivateKey().public_key(), network="regtest")
            dest_script = TransactionBuilder("regtest")._create_script_pubkey(dest_addr)

            bad_sig = b"\x30\x06\x02\x01\x01\x02\x01\x01\x01"  # malformed/invalid DER+flag
            pub = source_priv.public_key().to_bytes()
            script_sig = bytes([len(bad_sig)]) + bad_sig + bytes([len(pub)]) + pub

            tx = Transaction(version=2)
            tx.vin.append(
                TxIn(
                    prev_tx_hash=bytes.fromhex(prev_txid),
                    prev_tx_index=0,
                    script_sig=script_sig,
                )
            )
            tx.vout.append(TxOut(value=100000, script_pubkey=dest_script))

            accepted = await self.node.mempool.add_transaction(tx)
            self.assertFalse(accepted)

        asyncio.run(run_test())

    def test_block_validator_rejects_invalid_signature(self) -> None:
        async def run_test() -> None:
            self.assertTrue(await self.node.initialize())
            self.assertIsNotNone(self.node.utxo_store)
            self.assertIsNotNone(self.node.chainstate)

            source_priv = PrivateKey()
            source_addr = public_key_to_address(source_priv.public_key(), network="regtest")
            source_script = TransactionBuilder("regtest")._create_script_pubkey(source_addr)

            prev_txid = "22" * 32
            self.node.utxo_store.add_utxo(
                txid=prev_txid,
                index=0,
                value=150000,
                script_pubkey=source_script,
                height=1,
                is_coinbase=False,
            )

            dest_addr = public_key_to_address(PrivateKey().public_key(), network="regtest")
            dest_script = TransactionBuilder("regtest")._create_script_pubkey(dest_addr)
            pub = source_priv.public_key().to_bytes()
            bad_sig = b"\x30\x06\x02\x01\x01\x02\x01\x01\x01"
            script_sig = bytes([len(bad_sig)]) + bad_sig + bytes([len(pub)]) + pub

            tx = Transaction(version=2)
            tx.vin.append(
                TxIn(
                    prev_tx_hash=bytes.fromhex(prev_txid),
                    prev_tx_index=0,
                    script_sig=script_sig,
                )
            )
            tx.vout.append(TxOut(value=120000, script_pubkey=dest_script))

            validator = BlockValidator(
                self.node.config.get_network_params(),
                self.node.utxo_store,
                self.node.chainstate.block_index,
            )
            self.assertFalse(validator.validate_transaction(tx, height=200, is_coinbase=False))

        asyncio.run(run_test())

    def test_node_initializes_simple_wallet_and_wallet_rpcs(self) -> None:
        async def run_test() -> None:
            self.assertTrue(await self.node.initialize())
            self.assertIsNotNone(self.node.simple_wallet_manager)
            wallet = self.node.simple_wallet_manager.create_wallet()
            self.node.simple_wallet_manager.activate_wallet(wallet.private_key_hex)
            self.assertEqual(
                self.node.simple_wallet_manager.get_active_wallet().address,
                wallet.address,
            )
            self.assertIsNotNone(self.node.rpc_server)
            handlers = self.node.rpc_server.handlers
            for method in (
                "get_wallet_info",
                "get_balance",
                "get_new_address",
                "createwallet",
                "loadwallet",
                "listwallets",
                "activatewallet",
            ):
                self.assertIn(method, handlers)

        asyncio.run(run_test())

    def test_simple_wallet_create_has_mnemonic(self) -> None:
        wallet = SimpleWallet.create()
        self.assertTrue(bool(wallet.mnemonic))
        self.assertGreaterEqual(len(wallet.mnemonic.split()), 12)

    def test_send_flow_accepts_utxo_index_field(self) -> None:
        async def run_test() -> None:
            manager = SimpleWalletManager(self.node.config.get_datadir(), network="regtest")
            wallet = manager.create_wallet()
            manager.activate_wallet(wallet.private_key_hex)

            sender_spk = TransactionBuilder("regtest")._create_script_pubkey(wallet.address)

            class _Config:
                def __init__(self, datadir):
                    self._datadir = datadir

                def get_datadir(self):
                    return self._datadir

                @staticmethod
                def get(key, default=None):
                    if key == "network":
                        return "regtest"
                    return default

            class _ChainState:
                @staticmethod
                def get_balance(_address):
                    return 0

                def get_utxos_for_address(self, address, _limit=1000):
                    if address != wallet.address:
                        return []
                    return [
                        {
                            "txid": "33" * 32,
                            "index": 7,
                            "value": 50000,
                            "script_pubkey": sender_spk,
                            "height": 1,
                            "is_coinbase": False,
                        }
                    ]

                def get_utxo(self, txid, index):
                    if txid == ("33" * 32) and int(index) == 7:
                        return {
                            "txid": txid,
                            "index": 7,
                            "value": 50000,
                            "script_pubkey": sender_spk,
                            "height": 1,
                            "is_coinbase": False,
                        }
                    return None

            class _Mempool:
                def __init__(self):
                    self.last_tx = None
                    self.policy = type("Policy", (), {"min_relay_fee": 1})()

                async def add_transaction(self, tx):
                    self.last_tx = tx
                    return True

            node_stub = type(
                "NodeStub",
                (),
                {
                    "config": _Config(self.node.config.get_datadir()),
                    "chainstate": _ChainState(),
                    "mempool": _Mempool(),
                    "simple_wallet_manager": manager,
                },
            )()

            handlers = WalletHandlers(node_stub)
            recipient = public_key_to_address(PrivateKey().public_key(), network="regtest")
            txid = await handlers.send_to_address(recipient, 0.0001)
            self.assertTrue(txid)
            self.assertIsNotNone(node_stub.mempool.last_tx)
            self.assertEqual(len(node_stub.mempool.last_tx.vin), 1)
            self.assertEqual(node_stub.mempool.last_tx.vin[0].prev_tx_index, 7)

        asyncio.run(run_test())
