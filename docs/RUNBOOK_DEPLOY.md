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
3. Generate node config/bootstrap using the assistant (recommended):
   - `python scripts/mainnet_bootstrap_assistant.py --datadir ~/.berzcoin-mainnet-a --bootstrap-from-seeds <seed1>,<seed2> --probe-timeout-secs 2.0 --require-reachable --addnode <trusted-peer:8333> --dnsseed --dnsseeds <seed1>,<seed2>`
   - Alternative with existing bootstrap file:
     `python scripts/mainnet_bootstrap_assistant.py --datadir ~/.berzcoin-mainnet-a --bootstrap-source ./configs/bootstrap_nodes.json --addnode <trusted-peer:8333>`
3. Refresh bootstrap peers from real DNS seeds (with TCP health probes):
   - `python scripts/update_dns_seeds.py <seed1> <seed2> <seed3> --port 8333 --probe-timeout-secs 2.0 --require-reachable --output configs/bootstrap_nodes.json`
   - Copy generated `configs/bootstrap_nodes.json` to node datadir as `bootstrap_nodes.json`.
4. Validate config values:
   - `network`, `datadir`, `port`, `rpcbind`, `rpcport`
   - `activation_height_berz_softfork_bip34_strict = 180000`
   - `activation_height_berz_hardfork_tx_v2 = 180100`
   - `node_consensus_version = 2`, `enforce_hardfork_guardrails = true`
   - `sync_getdata_batch_size`, `sync_poll_interval_secs`, `blocks_cache_size`
   - Mempool operator knobs:
     - `mempool_min_relay_fee`
     - `mempool_rolling_floor_halflife_secs`
     - `mempool_max_size_bytes`, `mempool_max_weight`, `mempool_max_transactions`
     - `mempool_max_ancestors`, `mempool_max_descendants`
     - `mempool_max_ancestor_size_vbytes`, `mempool_max_descendant_size_vbytes`
     - `mempool_max_package_count`, `mempool_max_package_weight`
5. Start with process manager (`systemd` recommended).
6. Verify node endpoints:
   - `GET /health` returns HTTP 200.
   - `GET /ready` returns `{"ready": true}` before exposing traffic.
   - `GET /metrics/prometheus` contains `berzcoin_best_height`.
7. Verify peer/sync basics using RPC:
   - `get_network_info`
   - `get_blockchain_info`
8. Validate first-sync bootstrap on a fresh datadir:
   - `getblockchaininfo.blocks` must progress from `-1` to `>= 0`.
   - `getblockchaininfo.bestblockhash` must become non-null.
   - Dashboard `/blocks` should list block rows (not `No blocks`).

## Peer Bootstrap Defaults (Phase 5)
- Discovery priority is fixed and deterministic:
  1. `connect` (strict; overrides all other sources)
  2. `addnode`
  3. `bootstrap_nodes.json` (`bootstrap_file`)
  4. DNS seeds (`dnsseed` + `dnsseeds` or network defaults)
- Network defaults:
  - `mainnet`: no built-in DNS seeds (operator must set real `dnsseeds` or bootstrap peers)
  - `testnet`: no built-in DNS seeds (operator must set real `dnsseeds` or bootstrap peers)
  - `regtest`: no DNS defaults (explicit peers required)
- Startup safety:
  - On non-regtest, node fails fast if there is no viable discovery source.
  - Override only for controlled environments with `allow_missing_bootstrap = true`.
- Operator requirement:
  - DNS seeds and `bootstrap_nodes.json` entries must be real and reachable.
  - Do not ship placeholder peers such as `seed1.berzcoin.org`.

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
- Fresh-node bootstrap reaches at least genesis/tip anchor (`blocks >= 0`, non-null `bestblockhash`).
- No critical alerts firing from `ops/prometheus/alerts.yml`.
- `get_mempool_diagnostics` returns policy thresholds and non-empty eviction snapshot structure.
- Consensus activation keys are pinned uniformly on all production nodes before the mainnet deadline (**July 15, 2026 00:00:00 UTC**).

## Mainnet Soak Gate (Required Before Launch)
- Run a public-network soak for **24-72 hours** on at least one internet-connected node with real DNS/bootstrap peers.
- During soak, monitor and record:
  - peer count churn/recovery
  - sync lag and reorg depth
  - mempool growth/eviction rates
  - RPC liveness/readiness availability
- Minimum pre-launch command set in staging:
  - `BERZ_SOAK=1 BERZ_SOAK_ITERS=200 pytest -q tests/integration/test_fault_injection_soak.py -q`
  - `BERZ_CHAOS_LONG=1 BERZ_CHAOS_LONG_STEPS=1200 pytest -q tests/chaos/test_network_chaos_suite.py::TestNetworkChaosSuite::test_chaos_long_run -q`
- Store artifacts and sign-off with `docs/MAINNET_READINESS_REPORT_2026-04-08.md` as baseline evidence.
