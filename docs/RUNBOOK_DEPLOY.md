# BerzCoin Deploy Runbook

## Goal
Deploy a new node instance reproducibly with health and metrics checks.

## Inputs
- Release tag/commit SHA
- Node config file (`berzcoin.conf`)
- Datadir path and backup destination

## Steps
1. Checkout exact release SHA.
2. Install dependencies: `pip install -e ".[dev]"`.
3. Validate config values:
   - `network`, `datadir`, `port`, `rpcbind`, `rpcport`
   - `sync_getdata_batch_size`, `sync_poll_interval_secs`, `blocks_cache_size`
4. Start with process manager (`systemd` recommended).
5. Verify node endpoints:
   - `GET /health` returns HTTP 200.
   - `GET /ready` returns `{"ready": true}` before exposing traffic.
   - `GET /metrics/prometheus` contains `berzcoin_best_height`.
6. Verify peer/sync basics using RPC:
   - `get_network_info`
   - `get_blockchain_info`

## Acceptance checks
- Readiness passes.
- Peer count > 0 (unless isolated regtest).
- Sync lag trends toward zero.
- No critical alerts firing from `ops/prometheus/alerts.yml`.
