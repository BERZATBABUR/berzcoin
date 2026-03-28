"""Regression tests for block sync connect path."""

import asyncio
import unittest
from unittest.mock import patch

from node.p2p.sync import BlockSync
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut


class _Peer:
    address = "127.0.0.1:18444"


class _Store:
    def __init__(self):
        self.writes = []

    def write_block(self, block, height):
        self.writes.append((block, height))


class _BlockIndex:
    def __init__(self):
        self.added = []

    def add_block(self, block, height, chainwork):
        self.added.append((block, height, chainwork))


class _Params:
    @staticmethod
    def get_network_name():
        return "regtest"


class _HeaderChain:
    def __init__(self):
        self.added = []

    def add_header(self, header, height, chainwork):
        self.added.append((header, height, chainwork))


class _ChainWork:
    @staticmethod
    def calculate_chain_work(_headers):
        return 10


class _ChainState:
    def __init__(self, prev_hash_hex):
        self._best_height = 5
        self._best_hash = prev_hash_hex
        self._best_chainwork = 100
        self.blocks_store = _Store()
        self.block_index = _BlockIndex()
        self.utxo_store = object()
        self.params = _Params()
        self.chainwork = _ChainWork()
        self.header_chain = _HeaderChain()
        self.validated = []
        self.best_updates = []

    def get_best_height(self):
        return self._best_height

    def get_best_block_hash(self):
        return self._best_hash

    def get_best_chainwork(self):
        return self._best_chainwork

    def get_height(self, _block_hash):
        return None

    def validate_block_stateful(self, block, height):
        self.validated.append((block, height))
        return True

    def set_best_block(self, block_hash, height, chainwork):
        self.best_updates.append((block_hash, height, chainwork))


class _ConnectOk:
    def __init__(self, _utxo_store, _block_index, network="mainnet"):
        self.network = network

    def connect(self, _block):
        return True


def _coinbase_block(prev_hash_hex: str) -> Block:
    tx = Transaction(
        version=1,
        inputs=[
            TxIn(
                prev_tx_hash=b"\x00" * 32,
                prev_tx_index=0xFFFFFFFF,
                script_sig=b"\x02\x00",
                sequence=0xFFFFFFFF,
            )
        ],
        outputs=[TxOut(value=0, script_pubkey=b"")],
        locktime=0,
    )
    header = BlockHeader(
        version=1,
        prev_block_hash=bytes.fromhex(prev_hash_hex),
        merkle_root=tx.txid(),
        timestamp=1_700_000_000,
        bits=0x207fffff,
        nonce=0,
    )
    return Block(header=header, transactions=[tx])


class TestBlockSyncProcessBlock(unittest.TestCase):
    def test_process_block_uses_stateful_validation_and_indexes_before_connect(self):
        async def run():
            prev_hash = "11" * 32
            chain = _ChainState(prev_hash)
            sync = BlockSync(chain)
            block = _coinbase_block(prev_hash)

            with patch("node.p2p.sync.Block.deserialize", return_value=(block, 0)):
                with patch("node.validation.connect.ConnectBlock", _ConnectOk):
                    ok = await sync.process_block(_Peer(), b"raw")

            self.assertTrue(ok)
            self.assertEqual(chain.validated[0][1], 6)
            self.assertEqual(chain.blocks_store.writes[0][1], 6)
            self.assertEqual(chain.block_index.added[0][1], 6)
            self.assertEqual(chain.best_updates[0][1], 6)
            self.assertEqual(chain.best_updates[0][2], 110)
            self.assertEqual(chain.header_chain.added[0][1], 6)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
