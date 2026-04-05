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
   - Mempool operator knobs:
     - `mempool_min_relay_fee`
     - `mempool_rolling_floor_halflife_secs`
     - `mempool_max_size_bytes`, `mempool_max_weight`, `mempool_max_transactions`
     - `mempool_max_ancestors`, `mempool_max_descendants`
     - `mempool_max_ancestor_size_vbytes`, `mempool_max_descendant_size_vbytes`
     - `mempool_max_package_count`, `mempool_max_package_weight`
4. Start with process manager (`systemd` recommended).
5. Verify node endpoints:
   - `GET /health` returns HTTP 200.
   - `GET /ready` returns `{"ready": true}` before exposing traffic.
   - `GET /metrics/prometheus` contains `berzcoin_best_height`.
6. Verify peer/sync basics using RPC:
   - `get_network_info`
   - `get_blockchain_info`

## Peer Bootstrap Defaults (Phase 5)
- Discovery priority is fixed and deterministic:
  1. `connect` (strict; overrides all other sources)
  2. `addnode`
  3. `bootstrap_nodes.json` (`bootstrap_file`)
  4. DNS seeds (`dnsseed` + `dnsseeds` or network defaults)
- Network defaults:
  - `mainnet`: built-in non-empty DNS seed profile
  - `testnet`: built-in non-empty DNS seed profile
  - `regtest`: no DNS defaults (explicit peers required)
- Startup safety:
  - On non-regtest, node fails fast if there is no viable discovery source.
  - Override only for controlled environments with `allow_missing_bootstrap = true`.

## Operator Examples
1. Strict trusted peers only:
   - `connect = 203.0.113.10:8333,198.51.100.22:8333`
2. Mixed production bootstrap:
   - `addnode = 203.0.113.10:8333`
   - `bootstrap_enabled = true`
   - `bootstrap_file = bootstrap_nodes.json`
   - `dnsseed = true`
3. Air-gapped bring-up / manual lab:
   - `allow_missing_bootstrap = true` (non-regtest only when intentional)

## Acceptance checks
- Readiness passes.
- Peer count > 0 (unless isolated regtest).
- Sync lag trends toward zero.
- No critical alerts firing from `ops/prometheus/alerts.yml`.
- `get_mempool_diagnostics` returns policy thresholds and non-empty eviction snapshot structure.
