"""Acceptance-path hardening tests for block validation and reorg safety."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from node.app.main import BerzCoinNode
from node.p2p.sync import BlockSync
from node.rpc.handlers.mining import MiningHandlers
from shared.core.block import Block, BlockHeader
from shared.core.transaction import Transaction, TxIn, TxOut


def _coinbase_tx(tag: bytes = b"\x02\x01", value: int = 50_0000_0000) -> Transaction:
    tx = Transaction(version=1)
    tx.vin = [TxIn(prev_tx_hash=b"\x00" * 32, prev_tx_index=0xFFFFFFFF, script_sig=tag, sequence=0xFFFFFFFF)]
    tx.vout = [TxOut(value=value, script_pubkey=b"\x51")]
    return tx


def _block(prev_hash_hex: str, txs, bits: int = 0x207FFFFF, nonce: int = 0, merkle_root: bytes | None = None) -> Block:
    mr = merkle_root if merkle_root is not None else (txs[0].txid() if txs else b"\x00" * 32)
    hdr = BlockHeader(
        version=1,
        prev_block_hash=bytes.fromhex(prev_hash_hex),
        merkle_root=mr,
        timestamp=1_700_000_100,
        bits=bits,
        nonce=nonce,
    )
    return Block(header=hdr, transactions=list(txs))


class _Index:
    def __init__(self, entries):
        self.entries = dict(entries)

    def get_block(self, h):
        return self.entries.get(h)

    def add_block(self, block, height, chainwork, update_best=False):
        self.entries[block.header.hash_hex()] = SimpleNamespace(
            block_hash=block.header.hash_hex(),
            height=height,
            header=block.header,
            chainwork=chainwork,
        )


class _Orphanage:
    def cleanup_expired(self):
        return None

    def add_orphan(self, _block, source_peer=None):
        _ = source_peer
        return None


class TestBlockAcceptanceHardening(unittest.TestCase):
    def test_bad_pow_rejected_without_state_change_or_relay(self):
        async def run():
            prev_hash = "11" * 32
            parent = SimpleNamespace(
                block_hash=prev_hash,
                height=5,
                header=BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0),
                chainwork=100,
            )
            utxo_before = {"u": 1}
            relayed = []

            node = SimpleNamespace()
            node.orphanage = _Orphanage()
            node.connman = SimpleNamespace(broadcast_block=lambda _b: relayed.append(True))
            node.config = SimpleNamespace(get=lambda _k, d=None: d)
            node.chainstate = SimpleNamespace(
                block_index=_Index({prev_hash: parent}),
                get_best_block_hash=lambda: prev_hash,
                get_best_chainwork=lambda: 100,
                get_block=lambda _h: None,
                validate_block_stateful=lambda _b, _h: False,  # rejected in normal path
                chainwork=SimpleNamespace(calculate_chain_work=lambda _hs: 10),
                blocks_store=SimpleNamespace(write_block=lambda _b, _h: (_ for _ in ()).throw(RuntimeError("must not write"))),
                header_chain=SimpleNamespace(add_header=lambda *_a: None),
                utxo_store=SimpleNamespace(state=utxo_before),
            )
            node._remember_known_block = lambda _h: None
            node._process_orphan_children = lambda _h, relay=False: asyncio.sleep(0)
            node._connect_as_new_tip = lambda _b, _h, _w: asyncio.sleep(0, result=True)

            bad_pow_block = _block(prev_hash, [_coinbase_tx()], bits=0x1D00FFFF, nonce=0)
            accepted, _bh, reason = await BerzCoinNode.on_block(node, bad_pow_block, source_peer="p", relay=True)

            self.assertFalse(accepted)
            self.assertEqual(reason, "stateful_validation_failed")
            self.assertEqual(node.chainstate.get_best_block_hash(), prev_hash)
            self.assertEqual(node.chainstate.utxo_store.state, utxo_before)
            self.assertEqual(relayed, [])

        asyncio.run(run())

    def test_bad_merkle_rejected_without_tip_or_mempool_change(self):
        async def run():
            prev_hash = "22" * 32
            parent = SimpleNamespace(
                block_hash=prev_hash,
                height=3,
                header=BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0),
                chainwork=77,
            )
            mempool_state = {"txs": ["a", "b"]}
            node = SimpleNamespace()
            node.orphanage = _Orphanage()
            node.connman = None
            node.config = SimpleNamespace(get=lambda _k, d=None: d)
            node.chainstate = SimpleNamespace(
                block_index=_Index({prev_hash: parent}),
                get_best_block_hash=lambda: prev_hash,
                get_best_chainwork=lambda: 77,
                get_block=lambda _h: None,
                validate_block_stateful=lambda _b, _h: False,  # merkle invalid on validator path
                chainwork=SimpleNamespace(calculate_chain_work=lambda _hs: 10),
                blocks_store=SimpleNamespace(write_block=lambda *_a: (_ for _ in ()).throw(RuntimeError("must not write"))),
                header_chain=SimpleNamespace(add_header=lambda *_a: None),
            )
            node.mempool = SimpleNamespace(snapshot=mempool_state.copy())
            node._remember_known_block = lambda _h: None
            node._process_orphan_children = lambda _h, relay=False: asyncio.sleep(0)
            node._connect_as_new_tip = lambda _b, _h, _w: asyncio.sleep(0, result=True)

            bad_merkle = _block(prev_hash, [_coinbase_tx()], merkle_root=b"\xff" * 32)
            accepted, _bh, reason = await BerzCoinNode.on_block(node, bad_merkle, source_peer="p", relay=False)
            self.assertFalse(accepted)
            self.assertEqual(reason, "stateful_validation_failed")
            self.assertEqual(node.chainstate.get_best_block_hash(), prev_hash)
            self.assertEqual(node.mempool.snapshot, mempool_state)

        asyncio.run(run())

    def test_malformed_bits_rejected_before_connect_or_utxo_mutation(self):
        async def run():
            prev_hash = "12" * 32
            parent = SimpleNamespace(
                block_hash=prev_hash,
                height=8,
                header=BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0),
                chainwork=200,
            )
            utxo_before = {"u": 1}
            writes = []
            node = SimpleNamespace()
            node.orphanage = _Orphanage()
            node.connman = None
            node.config = SimpleNamespace(get=lambda _k, d=None: d)
            node.chainstate = SimpleNamespace(
                block_index=_Index({prev_hash: parent}),
                get_best_block_hash=lambda: prev_hash,
                get_best_chainwork=lambda: 200,
                get_block=lambda _h: None,
                validate_block_stateful=lambda _b, _h: False,  # malformed bits rejected on validator path
                chainwork=SimpleNamespace(calculate_chain_work=lambda _hs: 10),
                blocks_store=SimpleNamespace(write_block=lambda *_a: writes.append(True)),
                header_chain=SimpleNamespace(add_header=lambda *_a: writes.append(True)),
                utxo_store=SimpleNamespace(state=utxo_before),
            )
            node._remember_known_block = lambda _h: None
            node._process_orphan_children = lambda _h, relay=False: asyncio.sleep(0)
            node._connect_as_new_tip = lambda _b, _h, _w: asyncio.sleep(0, result=True)

            malformed_bits_block = _block(prev_hash, [_coinbase_tx()], bits=0x00000000, nonce=1)
            accepted, _bh, reason = await BerzCoinNode.on_block(node, malformed_bits_block, source_peer="p", relay=False)
            self.assertFalse(accepted)
            self.assertEqual(reason, "stateful_validation_failed")
            self.assertEqual(node.chainstate.utxo_store.state, utxo_before)
            self.assertEqual(writes, [])

        asyncio.run(run())

    def test_side_branch_invalid_tx_cannot_activate_reorg(self):
        async def run():
            fork_parent_hash = "33" * 32
            active_tip_hash = "66" * 32
            tip = SimpleNamespace(
                block_hash=fork_parent_hash,
                height=10,
                header=BlockHeader(prev_block_hash=b"\x00" * 32, merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0),
                chainwork=100,
            )
            active_tip = SimpleNamespace(
                block_hash=active_tip_hash,
                height=11,
                header=BlockHeader(prev_block_hash=bytes.fromhex(fork_parent_hash), merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0),
                chainwork=150,
            )
            parent = SimpleNamespace(
                block_hash=fork_parent_hash,
                height=10,
                header=tip.header,
                chainwork=100,
            )
            side_header = BlockHeader(prev_block_hash=bytes.fromhex(fork_parent_hash), merkle_root=b"\x00" * 32, bits=0x207FFFFF, nonce=0)
            side_block = Block(header=side_header, transactions=[_coinbase_tx()])

            idx = _Index({fork_parent_hash: tip, active_tip_hash: active_tip})
            node = SimpleNamespace()
            node.orphanage = _Orphanage()
            node.connman = None
            node.config = SimpleNamespace(get=lambda _k, d=None: d)
            node.chainstate = SimpleNamespace(
                block_index=idx,
                get_best_block_hash=lambda: active_tip_hash,
                get_best_chainwork=lambda: 150,
                get_block=lambda h: side_block if h == side_block.header.hash_hex() else (object() if h in (fork_parent_hash, active_tip_hash) else None),
                validate_block_stateful=lambda _b, h: h <= 10,  # fail candidate side block at h=11
                rules=SimpleNamespace(validate_block=lambda *_a: None),
                chainwork=SimpleNamespace(calculate_chain_work=lambda _hs: 100),
                blocks_store=SimpleNamespace(write_block=lambda _b, _h: None),
                header_chain=SimpleNamespace(add_header=lambda *_a: None),
                set_best_block=lambda *_a: (_ for _ in ()).throw(RuntimeError("must not switch tip")),
                utxo_store=SimpleNamespace(),
            )
            node._remember_known_block = lambda _h: None
            node._process_orphan_children = lambda _h, relay=False: asyncio.sleep(0)
            node._reconcile_mempool_after_reorg = lambda *_a: asyncio.sleep(0, result={})
            node._index_connected_block = lambda *_a: None
            node._connect_as_new_tip = lambda _b, _h, _w: asyncio.sleep(0, result=True)

            with patch("node.chain.reorg.ReorgManager") as RM:
                rm = RM.return_value
                rm.can_reorganize.return_value = False
                accepted, _bh, reason = await BerzCoinNode.on_block(node, side_block, source_peer=None, relay=False)
                self.assertFalse(accepted)
                self.assertEqual(reason, "reorg_preflight_failed")
                # ensure fork validation callback was provided
                self.assertTrue(rm.can_reorganize.call_args.kwargs.get("validate_connect_block") is not None)

        asyncio.run(run())

    def test_p2p_rpc_and_miner_submit_use_same_on_block_path(self):
        async def run():
            calls = []
            prev_hash = "55" * 32
            block = _block(prev_hash, [_coinbase_tx()])
            block_hex = block.serialize().hex()

            async def on_block(_block, source_peer=None, relay=False):
                _ = source_peer, relay
                calls.append("on_block")
                return True, _block.header.hash_hex(), None

            node = SimpleNamespace(
                on_block=on_block,
                chainstate=SimpleNamespace(get_best_height=lambda: 0),
                config=SimpleNamespace(get=lambda k, d=None: "regtest" if k == "network" else d),
                miner=SimpleNamespace(mine_single_block=lambda _addr: asyncio.sleep(0, result=block)),
                mempool=SimpleNamespace(transactions={}, get_transactions_for_block=lambda _w: asyncio.sleep(0, result=[])),
            )

            # RPC submit path
            m = MiningHandlers(node)
            await m.submit_block(block_hex)

            # Miner path (generate_to_address -> generate -> on_block)
            await m.generate_to_address(1, "bcrt1qexample")

            # P2P sync path (block_handler wired to on_block)
            sync = BlockSync(chainstate=SimpleNamespace(get_height=lambda _h: None), block_handler=on_block)
            peer = SimpleNamespace(address="127.0.0.1:18444")
            await sync.process_block(peer, block.serialize())

            self.assertGreaterEqual(len(calls), 3)

        asyncio.run(run())

    def test_blocksync_refuses_non_unified_local_connect_fallback(self):
        async def run():
            sync = BlockSync(chainstate=SimpleNamespace(), block_handler=None)
            peer = SimpleNamespace(address="127.0.0.1:18444")
            prev_hash = "77" * 32
            block = _block(prev_hash, [_coinbase_tx()])
            ok = await sync.process_block(peer, block.serialize())
            self.assertFalse(ok)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
