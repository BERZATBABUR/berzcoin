# BerzCoin Architecture (v0.1)

This document describes the current architecture in this repository.

## Scope

v0.1 targets a validation-first full node with a private-key wallet model.

Implemented scope:
- Full node process (`node/app/main.py`) with chainstate, mempool, P2P, RPC, optional web dashboard.
- Canonical wallet activation by private key (`SimpleWalletManager`).
- UTXO-based transaction validation and block connection/disconnection.
- CPU mining path (primarily regtest workflow) with fee + subsidy coinbase reward.

Out of scope for v0.1:
- Legacy lightwallet/stratum ecosystem.
- Bitcoin Core-level script/tapscript parity.
- Production-grade p2p hardening parity.

## High-Level Components

- `shared/`
  - `core/`: blocks, transactions, serialization, merkle/hash helpers.
  - `consensus/`: params, PoW, subsidy, and rule helpers.
  - `script/`: script VM and verification helpers.
  - `protocol/`: p2p message/codec primitives.
- `node/`
  - `app/`: bootstrap, config, startup wiring.
  - `chain/`: chainstate, block index, reorg logic, validation pipeline.
  - `storage/`: SQLite schema and stores (`blocks`, `outputs`, `utxo`, peers, etc.).
  - `mempool/`: mempool pool, policy, limits, package handling.
  - `p2p/`: connection manager, peer handling, sync/orphan processing.
  - `rpc/`: JSON-RPC server and handlers.
  - `web/`: optional minimal dashboard.
  - `wallet/`: private-key wallet manager used by node.

## Core Data Flow

1. Transaction intake:
- P2P or RPC receives tx.
- Validation checks inputs/signatures/value rules.
- Mempool policy filters standardness and fee policy.
- Accepted tx is relayed and becomes miner candidate.

2. Block intake:
- Block is validated against consensus and chainstate.
- If it extends tip: connect block, update UTXO, clean/revalidate mempool.
- If side branch has more work: run reorg manager (disconnect old branch, connect new branch).

3. Mining:
- Miner assembles block from mempool by fee rate.
- Coinbase pays `subsidy + fees` to configured mining address.
- PoW loop searches nonce/extra-nonce.
- Found block is submitted through the same node acceptance path.

## Storage Model

SQLite tables include:
- `blocks`, `block_headers`, `transactions`, `inputs`, `outputs`
- `utxo` as current spendable state
- `peers`, `bans`, `checkpoints`, `settings`

Design note:
- `utxo` is the live spendable set.
- `outputs` tracks historical outputs and spent markers.

## Reorg and Recovery

Current implementation:
- Reorg manager enforces max disconnect depth and path invariants.
- Reorg preflight can simulate and rollback.
- Disconnect path restores spent outputs and removes disconnected outputs.
- Reindexer performs transactional replay + UTXO rebuild from main chain heights.

Still needed for production depth:
- More adversarial long-run reorg/fault-injection scenarios.
- Stronger proofs/tooling for crash-consistency invariants.

## Configuration Model

Important:
- Node loader uses Python `ConfigParser` (INI-style sections/keys).
- Some files use `.toml` extension as profile templates, but key/value semantics are INI-compatible for current loader usage.
- Canonical runtime config remains `berzcoin.conf` in datadir.

## Security Posture (Current)

- RPC includes IP allowlist and HTTP basic auth/cookie auth path.
- Validation-first block/tx processing is enforced.
- Baseline p2p scoring/eviction exists.

Not yet parity with Bitcoin Core hardening:
- Full anti-eclipse diversity strategy.
- Full compact block + relay policy parity.
- Full script/tapscript historical edge-case parity.
