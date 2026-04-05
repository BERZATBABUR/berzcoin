# BerzCoin Operations Playbook

## Health and Readiness
- RPC liveness: `GET /health`
- RPC readiness: `GET /ready`
- Structured metrics: `GET /metrics`
- Prometheus metrics: `GET /metrics/prometheus`
- RPC health details: `get_health`
- RPC readiness gate: `get_readiness`
- Metrics snapshot: `get_metrics`

## SLO Signals
- `berzcoin_readiness_slo`: target `1` for healthy service.
- `berzcoin_sync_lag_slo`: target `1` (`sync_lag_blocks <= health_sync_lag_warn_blocks`).
- `berzcoin_sync_lag_blocks`: should trend to zero during steady-state.
- `berzcoin_peer_count`: should stay above `health_min_peers_warn`.

See alert policy in `ops/prometheus/alerts.yml` and dashboard in
`ops/grafana/berzcoin-node-dashboard.json`.

## Startup Sequence
1. Start node with database and chainstate initialized.
2. Verify `get_readiness` returns `{"ready": true}` before exposing write traffic.
3. Confirm peer count and chain height with `get_info` and `get_network_info`.

## Backup and Recovery
1. Create wallet backup with `backup_wallet` RPC or wallet API `create_backup`.
2. Store backup artifacts in a separate host/volume.
3. Recover using `restore_wallet` (or wallet `restore_backup`) and verify with `get_wallet_info`.

## Incident Response
- High orphan/reorg activity:
  - Pause external writers.
  - Inspect peer scores and connected peers.
  - Validate best chain progression and mempool acceptance reasons.
- Resource pressure:
  - Check `get_metrics` memory and mempool size.
  - Lower inbound/outbound caps or restart with reduced load.

## Mempool Pressure Playbook
1. Diagnose quickly:
   - `get_mempool_info` for high-level state.
   - `get_mempool_diagnostics` for:
     - reject histograms (`reject_reasons`, `reject_reasons_top`)
     - eviction histograms (`eviction_reasons`, `eviction_reasons_top`)
     - policy thresholds (`policy_thresholds`)
     - ranked `eviction_snapshot` candidates.
2. Determine pressure type:
   - Spam/low-fee flood: `fee_too_low` rejects rise and `mempool_space` evictions rise.
   - Conflict storm: `rbf_policy` and `utxo_already_spent_in_mempool` rise.
   - Reorg fallout: `reorg_*` eviction reasons rise.
3. Tune without code changes (config + restart):
   - Raise `mempool_min_relay_fee` to increase ingress floor.
   - Lower `mempool_max_transactions` / `mempool_max_size_bytes` for tighter memory bound.
   - Tighten package/dependency limits:
     - `mempool_max_ancestors`, `mempool_max_descendants`
     - `mempool_max_package_count`, `mempool_max_package_weight`
   - Adjust rolling floor decay with `mempool_rolling_floor_halflife_secs`.
4. Verify stabilization:
   - Peak mempool size/weight stops climbing.
   - High-fee tx inclusion resumes.
   - Reject/eviction histograms trend down from incident peak.

## Upgrade Procedure
1. Stop node cleanly via RPC `stop`.
2. Backup wallet and data directory metadata.
3. Deploy new release.
4. Start node and verify `get_health`, `get_readiness`, and chain tip continuity.

Detailed procedures:
- `docs/RUNBOOK_DEPLOY.md`
- `docs/RUNBOOK_UPGRADE.md`
- `docs/RUNBOOK_INCIDENTS.md`
- `docs/RUNBOOK_CONSENSUS_ACTIVATION.md`
