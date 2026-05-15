"""RPC hardening regression tests."""

import asyncio
import unittest
from types import SimpleNamespace

from node.rpc.handlers.blockchain import BlockchainHandlers
from node.rpc.handlers.mempool import MempoolHandlers
from node.rpc.handlers.mining import MiningHandlers
from node.rpc.server import RPCServer
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut


def _sample_tx(seed: bytes = b"\x11") -> Transaction:
    return Transaction(
        version=1,
        inputs=[TxIn(prev_tx_hash=seed * 32, prev_tx_index=0, script_sig=b"\x51", sequence=0xFFFFFFFF)],
        outputs=[TxOut(value=1000, script_pubkey=b"\x51")],
        locktime=0,
    )


class _RequestStub:
    def __init__(self, body, auth_header=None):
        self._body = body
        self.headers = {}
        if auth_header is not None:
            self.headers["Authorization"] = auth_header
        self.remote = "127.0.0.1"

    async def json(self):
        return self._body


class TestRPCServerHardening(unittest.IsolatedAsyncioTestCase):
    async def test_method_not_found_and_invalid_params(self):
        server = RPCServer()

        async def add(a: int, b: int) -> int:
            return int(a) + int(b)

        server.register_handler("add", add)
        not_found = await server._process_request(
            {"jsonrpc": "2.0", "method": "missing", "params": [], "id": 1}
        )
        self.assertEqual(not_found["error"]["code"], -32601)

        invalid = await server._process_request(
            {"jsonrpc": "2.0", "method": "add", "params": [1], "id": 2}
        )
        self.assertEqual(invalid["error"]["code"], -32602)

    async def test_auth_required(self):
        server = RPCServer()
        req = _RequestStub({"jsonrpc": "2.0", "method": "stop", "params": [], "id": 1})
        resp = await server._handle_request(req)
        self.assertEqual(resp.status, 401)

    async def test_concurrent_requests(self):
        server = RPCServer()
        values = []
        lock = asyncio.Lock()

        async def push(v: int):
            async with lock:
                values.append(int(v))
            return True

        server.register_handler("push", push)
        reqs = [
            {"jsonrpc": "2.0", "method": "push", "params": [i], "id": i}
            for i in range(100)
        ]
        await asyncio.gather(*(server._process_request(r) for r in reqs))
        self.assertEqual(len(values), 100)
        self.assertEqual(set(values), set(range(100)))


class TestRPCHandlersHardening(unittest.IsolatedAsyncioTestCase):
    async def test_sendrawtransaction_paths(self):
        tx = _sample_tx()
        tx_hex = tx.serialize().hex()
        txid = tx.txid().hex()

        class _Node:
            connman = None
            mempool = SimpleNamespace()

            async def on_transaction(self, tx_obj, relay=True):
                got = tx_obj.txid().hex()
                if got == txid:
                    return True, got, ""
                return False, got, "invalid"

        handler = MempoolHandlers(_Node())
        accepted = await handler.send_raw_transaction(tx_hex)
        self.assertEqual(accepted, txid)
        with self.assertRaises(Exception):
            await handler.send_raw_transaction("00")

    async def test_getrawtransaction_mempool_and_confirmed(self):
        mempool_tx = _sample_tx(b"\x22")
        confirmed_tx = _sample_tx(b"\x33")
        hdr = BlockHeader(merkle_root=b"\x00" * 32)
        block = Block(hdr, [confirmed_tx])
        block_hash = block.header.hash_hex()

        class _Mempool:
            async def get_transaction(self, txid):
                return mempool_tx if txid == mempool_tx.txid().hex() else None

        class _Chain:
            def get_best_height(self):
                return 1

            def get_best_block_hash(self):
                return block_hash

            def get_best_chainwork(self):
                return 1

            def get_block(self, h):
                return block if h == block_hash else None

            def get_confirmations(self, _txid):
                return 1

            def get_transaction(self, txid):
                if txid == confirmed_tx.txid().hex():
                    return {"block_hash": block_hash, "block_tx_index": 0}
                return None

        node = SimpleNamespace(chainstate=_Chain(), mempool=_Mempool(), network="regtest", tx_indexer=None)
        handler = BlockchainHandlers(node)
        mem_hex = await handler.get_raw_transaction(mempool_tx.txid().hex(), False)
        self.assertEqual(mem_hex, mempool_tx.serialize().hex())
        conf = await handler.get_raw_transaction(confirmed_tx.txid().hex(), True)
        self.assertEqual(conf["txid"], confirmed_tx.txid().hex())
        self.assertEqual(conf["blockhash"], block_hash)

    async def test_block_methods_and_generate_to_address(self):
        mined_tx = _sample_tx(b"\x44")
        mined_block = Block(BlockHeader(merkle_root=b"\x00" * 32), [mined_tx])
        mined_hash = mined_block.header.hash_hex()

        class _Chain:
            params = SimpleNamespace(max_block_weight=4_000_000, pow_limit=(1 << 255))

            def get_block(self, h):
                return mined_block if h == mined_hash else None

            def get_height(self, h):
                return 0 if h == mined_hash else None

            def get_best_height(self):
                return 0

            def get_best_block_hash(self):
                return mined_hash

            def get_best_chainwork(self):
                return 1

            def get_block_by_height(self, h):
                return mined_block if h == 0 else None

            def get_header_by_hash(self, h):
                return mined_block.header if h == mined_hash else None

            def get_header(self, _h):
                return mined_block.header

        class _Miner:
            async def mine_single_block(self, _address):
                return mined_block

        class _Node:
            def __init__(self):
                self.network = "regtest"
                self.chainstate = _Chain()
                self.mempool = SimpleNamespace(transactions={}, get_transactions_for_block=self._txs)
                self.miner = _Miner()
                self.config = SimpleNamespace(get=lambda k, d=None: "regtest" if k == "network" else d)

            async def _txs(self, _max_w):
                return []

            async def on_block(self, _block, source_peer=None, relay=False):
                return True, mined_hash, ""

        node = _Node()
        b = BlockchainHandlers(node)
        self.assertEqual(await b.get_block_hash(0), mined_hash)
        self.assertEqual((await b.get_block_header(mined_hash))["hash"], mined_hash)
        self.assertEqual((await b.get_block(mined_hash, 1))["hash"], mined_hash)

        m = MiningHandlers(node)
        generated = await m.generate_to_address(1, "bcrt1qexampleaddress")
        self.assertEqual(generated, [mined_hash])


if __name__ == "__main__":
    unittest.main()
