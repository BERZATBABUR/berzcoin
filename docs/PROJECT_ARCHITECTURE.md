# BerzCoin Project Architecture

This document maps major blockchain components to their implementation files.

## Cryptography

- Hashing:
  - `shared/core/hashes.py`
  - `shared/core/merkle.py`
  - `shared/crypto/address.py`
  - `shared/crypto/base58.py`
- Public/private keys:
  - `shared/crypto/keys.py`
  - `shared/crypto/secp256k1.py`
  - `shared/crypto/hd.py`
  - `shared/crypto/xpub.py`
- Digital signatures:
  - `shared/crypto/signatures.py`
  - `shared/crypto/secp256k1.py`
  - `shared/script/sigchecks.py`

## Transactions

- Transaction model, inputs/outputs, serialization, TXID:
  - `shared/core/transaction.py`
  - `shared/core/serialization.py`
- Scripts (`ScriptPubKey`, `ScriptSig`, locking/unlocking):
  - `shared/script/verify.py`
  - `shared/script/engine.py`
  - `shared/script/opcodes.py`
  - `shared/script/script_flags.py`
  - `shared/script/sigchecks.py`

## UTXO Model

- UTXO storage and retrieval:
  - `node/storage/utxo_store.py`
  - `node/chain/chainstate.py`
- Spending validation and double-spend prevention:
  - `node/validation/connect.py`
  - `node/chain/validation.py`
  - `node/mempool/pool.py`
  - `node/mempool/policy.py`

## Mempool

- Unconfirmed transaction pool:
  - `node/mempool/pool.py`
- Relay and fee policy:
  - `node/p2p/relay.py`
  - `node/mempool/policy.py`
  - `node/mempool/fees.py`
  - `node/mempool/limits.py`

## Blocks and Mining

- Block body/header and Merkle root:
  - `shared/core/block.py`
  - `shared/core/merkle.py`
- Candidate block creation, coinbase, nonce search:
  - `node/mining/block_assembler.py`
  - `node/mining/miner.py`
- Proof of Work, target, difficulty:
  - `shared/consensus/pow.py`
  - `node/mining/difficulty.py`

## Blockchain Structure

- Block linking and previous hash:
  - `node/chain/block_index.py`
  - `node/chain/headers.py`
- Chain history and reorg handling:
  - `node/chain/chainstate.py`
  - `node/chain/chainwork.py`
  - `node/chain/reorg.py`

## Nodes

- Full node runtime and orchestration:
  - `node/app/main.py`
  - `node/app/bootstrap.py`
  - `node/app/config.py`
- Validation:
  - `node/chain/validation.py`
  - `shared/consensus/rules.py`
- Storage:
  - `node/storage/db.py`
  - `node/storage/schema.py`
  - `node/storage/blocks_store.py`
  - `node/storage/mempool_store.py`
  - `node/storage/peers_store.py`
- Relay:
  - `node/p2p/connman.py`
  - `node/p2p/peer.py`
  - `node/p2p/relay.py`

## Consensus Rules

- Transaction and block validity:
  - `shared/consensus/rules.py`
  - `node/chain/validation.py`
- Chain acceptance and network parameters:
  - `shared/consensus/params.py`
  - `shared/consensus/versionbits.py`
  - `shared/consensus/deployments.py`

## P2P Network

- Node discovery:
  - `node/p2p/addrman.py`
  - `node/p2p/dns_seeds.py`
- Message protocol and propagation:
  - `shared/protocol/messages.py`
  - `shared/protocol/codec.py`
  - `node/p2p/connman.py`
  - `node/p2p/sync.py`

## Rewards System

- Block subsidy and halving:
  - `shared/consensus/subsidy.py`
- Transaction fees and miner reward composition:
  - `node/mining/block_assembler.py`
  - `node/mempool/fees.py`

## Difficulty Adjustment

- Difficulty and block-time stability:
  - `node/mining/difficulty.py`
  - `shared/consensus/pow.py`
  - `shared/consensus/params.py`

## Wallet Layer

- Key and address generation:
  - `shared/crypto/keys.py`
  - `shared/crypto/address.py`
  - `shared/crypto/hd.py`
  - `shared/crypto/xpub.py`
- Balance from UTXOs and transaction creation:
  - `node/wallet/simple_wallet.py`
  - `node/wallet/core/utxo_tracker.py`
  - `node/wallet/core/tx_builder.py`

## User Interface and Applications

- Wallet/miner web UI:
  - `node/web/mining_wallet_dashboard.py`
- RPC/API:
  - `node/rpc/server.py`
  - `node/rpc/register.py`
  - `node/rpc/auth.py`
  - `node/rpc/handlers/`
- CLI applications:
  - `cli/main.py`
  - `cli/wallet_standalone.py`
- Explorer-oriented indexing backend:
  - `node/indexer/txindex.py`
  - `node/indexer/addressindex.py`

