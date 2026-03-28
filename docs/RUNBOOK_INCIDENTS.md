# BerzCoin Incident Runbook

## Common incidents

### 1) Node not ready / persistent sync lag
1. Check `GET /ready` and `GET /health` details.
2. Verify peers (`get_network_info`) and lag (`berzcoin_sync_lag_blocks`).
3. Add trusted peers with `addnode`/`connect` config if discovery is weak.
4. If lag remains critical, restart process and monitor request backlog.

### 2) No peers
1. Validate firewall and bind/port settings.
2. Confirm `bootstrap_nodes.json` exists in datadir when bootstrap is enabled.
3. Temporarily add known-good peers.

### 3) DB integrity warnings
1. Run health check details and inspect database consistency output.
2. Stop node and take forensic copy of datadir.
3. Restore from known-good backup if integrity fails.
4. Reindex/rebuild as needed before returning to service.

## Post-incident
- Document timeline, blast radius, root cause, and mitigations.
- Add/update alert thresholds if detection lagged.
