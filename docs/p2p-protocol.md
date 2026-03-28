# BerzCoin P2P Protocol (Current v0.1)

This document describes the current node-to-node networking behavior.

## Transport

- TCP peer connections (inbound + outbound).
- Default ports by network profile:
  - mainnet: 8333
  - testnet: 18333
  - regtest: 18444

## Handshake and Message Primitives

Protocol message structures live in `shared/protocol/messages.py`.
Core message families:
- Handshake: `version`, `verack`.
- Chain sync: `getheaders`, `headers`, `getblocks`, `inv`, `getdata`, `block`.
- Transaction relay: `tx`, `inv`, `getdata`.
- Peer discovery: `addr`, `getaddr`.
- Liveness: `ping`, `pong`.
- Optional compact block negotiation scaffolding: `sendcmpct`.

## Peer Management

Connection manager (`node/p2p/connman.py`) handles:
- Outbound target and inbound limit.
- Netgroup diversity caps for outbound/inbound.
- DNS seed/bootstrap/static peer ingestion.
- Basic score-based bad peer eviction.

## Discovery Sources

Node can discover peers from:
- `addnode` and `connect` config entries.
- `bootstrap_nodes.json` (datadir).
- DNS seeds when `dnsseed=true` and `dnsseeds` populated.

## Relay Rules (Current)

- Received blocks/txs are validated before acceptance into local state.
- Accepted data is relayed to peers.
- Orphans are stored and retried once parents arrive.

## Security Baseline

Current baseline includes:
- Connection limits.
- Basic abuse scoring/ban path.
- Inbound netgroup and per-IP bounds.

Still missing for production parity:
- Full anti-eclipse strategy depth.
- Full compact block and relay policy parity with Bitcoin Core.
- Full adversarial p2p fuzz/soak corpus.
