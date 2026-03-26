#!/usr/bin/env python3
"""Test send/receive functionality on regtest.

Requires an existing chain tip (e.g. genesis already in the index). For a fresh
datadir, mine or import a genesis block first; otherwise mining steps will fail
with "No chain tip found".
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from node.app.main import BerzCoinNode
from node.mining.block_assembler import BlockAssembler
from node.mining.miner import MiningNode
from node.rpc.handlers.mining import MiningHandlers


async def test_send_receive() -> None:
    """Test sending and receiving BerzCoin."""
    print("=== BerzCoin Send/Receive Test ===\n")

    datadir = os.environ.get("BERZCOIN_TEST_DATADIR", "/tmp/berzcoin_test")
    os.makedirs(datadir, exist_ok=True)

    node = BerzCoinNode()
    node.config.set("network", "regtest")
    node.config.set("datadir", datadir)
    node.config.set("disablewallet", False)
    node.config.set("wallet", "test_send_receive")
    node.config.set("mining", False)
    node.config.set("autominer", False)
    node.config.set("debug", True)

    print("1. Initializing node...")
    if not await node.initialize():
        print("Failed to initialize node")
        return

    if node.wallet and node.wallet.locked:
        node.wallet.unlock("")

    print("2. Creating wallet / addresses...")
    if not node.wallet:
        print("No wallet loaded")
        if node.db:
            node.db.disconnect()
        return

    addr1 = node.wallet.get_new_address()
    addr2 = node.wallet.get_new_address()
    if not addr1 or not addr2:
        print("Could not obtain addresses (wallet locked or keypool empty)")
        if node.db:
            node.db.disconnect()
        return

    print(f"   Address 1: {addr1}")
    print(f"   Address 2: {addr2}")

    if node.chainstate.get_best_height() < 0:
        print(
            "\nNo chain tip (height < 0). Seed genesis or use a datadir with blocks first."
        )
        if node.db:
            node.db.disconnect()
        return

    if not node.mempool:
        print("Mempool unavailable (light mode?).")
        if node.db:
            node.db.disconnect()
        return

    print("\n3. Mining initial blocks (regtest generate)...")
    block_assembler = BlockAssembler(
        node.chainstate,
        node.mempool,
        addr1,
        network="regtest",
    )
    # Use new MiningNode with explicit mempool
    node.miner = MiningNode(node.chainstate, node.mempool, addr1)

    mining_handlers = MiningHandlers(node)
    mined = await mining_handlers.generate(101, addr1)
    for h in mined[:3]:
        print(f"   Block hash: {h[:16]}...")
    if len(mined) > 3:
        print(f"   ... and {len(mined) - 3} more")

    balance = node.wallet.get_balance()
    print(
        f"\n4. Balance after mining: {balance} satoshis ({balance / 100_000_000} BTC)"
    )

    if balance == 0:
        print(
            "   No wallet balance (coinbase UTXOs are on-chain; wallet may not track them yet)."
        )

    print(f"\n5. Sending 10 BTC to {addr2}...")
    amount = 10 * 100_000_000
    txid = await node.wallet.send_to_address(addr2, amount)

    if txid:
        print(f"   Transaction sent: {txid[:16]}...")

        await asyncio.sleep(2)

        print("\n6. Mining confirmation block...")
        mined2 = await mining_handlers.generate(1, addr1)
        if mined2:
            await asyncio.sleep(1)
            balance1 = node.wallet.get_balance()
            print("\n7. Final balances:")
            print(f"   Wallet balance: {balance1} satoshis ({balance1 / 100_000_000} BTC)")

            tx_info = node.chainstate.get_transaction(txid)
            if tx_info and tx_info.get("block_height") is not None:
                print(f"   Transaction in block height {tx_info['block_height']}")
                print("\nSend/Receive test completed (transaction indexed on chain).")
            else:
                print("   Transaction not found in chain DB (may be mempool-only).")
        else:
            print("   Failed to mine confirmation block")
    else:
        print("   Failed to send transaction (insufficient funds or signing error)")

    print("\n8. Stopping node...")
    await node.stop()
    if node.db:
        node.db.disconnect()
    print("Test complete")


if __name__ == "__main__":
    asyncio.run(test_send_receive())
