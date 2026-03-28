"""Regression: mined coinbase value must include selected mempool fees."""

import asyncio
import time
import unittest

from node.mining.miner import MiningNode
from node.wallet.core.tx_builder import TransactionBuilder
from shared.consensus.params import ConsensusParams
from shared.consensus.subsidy import get_block_subsidy
from shared.crypto.address import public_key_to_address
from shared.crypto.keys import PrivateKey
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut


class _Mempool:
    def __init__(self, txs):
        self._txs = txs

    async def get_transactions(self):
        return self._txs

    async def remove_transaction(self, _txid):
        return None


class _ChainState:
    def __init__(self, tip: Block):
        self.params = ConsensusParams.regtest()
        self.network = "regtest"
        self._tip = tip

    def get_best_height(self):
        return 0

    def get_best_block_hash(self):
        return self._tip.header.hash_hex()

    def get_header(self, _height):
        return self._tip.header

    def get_block_by_height(self, _height):
        return self._tip

    @staticmethod
    def get_utxo(_txid, _index):
        return {"value": 100_000}


class TestMiningFeeInclusion(unittest.TestCase):
    def test_coinbase_includes_mempool_fees(self) -> None:
        async def run() -> None:
            tip_header = BlockHeader(
                version=1,
                prev_block_hash=b"\x00" * 32,
                merkle_root=b"\x11" * 32,
                timestamp=int(time.time()) - 120,
                bits=0x207FFFFF,
                nonce=0,
            )
            tip_block = Block(tip_header, [])

            tx = Transaction(version=2)
            tx.vin.append(TxIn(prev_tx_hash=b"\x22" * 32, prev_tx_index=0, script_sig=b"", sequence=0xFFFFFFFF))
            tx.vout.append(TxOut(value=99_000, script_pubkey=b"\x51"))

            chainstate = _ChainState(tip_block)
            mempool = _Mempool([tx])
            mining_address = public_key_to_address(PrivateKey().public_key())
            miner = MiningNode(chainstate, mempool, mining_address)

            block = await miner.mine_single_block(mining_address)
            self.assertIsNotNone(block)
            assert block is not None

            expected_subsidy = get_block_subsidy(1, chainstate.params)
            self.assertEqual(block.transactions[0].vout[0].value, expected_subsidy + 1_000)
            expected_script = TransactionBuilder("regtest")._create_script_pubkey(mining_address)
            self.assertEqual(block.transactions[0].vout[0].script_pubkey, expected_script)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
