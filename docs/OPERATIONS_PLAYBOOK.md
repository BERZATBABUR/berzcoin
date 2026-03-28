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

## Upgrade Procedure
1. Stop node cleanly via RPC `stop`.
2. Backup wallet and data directory metadata.
3. Deploy new release.
4. Start node and verify `get_health`, `get_readiness`, and chain tip continuity.

Detailed procedures:
- `docs/RUNBOOK_DEPLOY.md`
- `docs/RUNBOOK_UPGRADE.md`
- `docs/RUNBOOK_INCIDENTS.md`
