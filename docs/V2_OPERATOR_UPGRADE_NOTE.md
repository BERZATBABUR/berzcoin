# BerzCoin v2 Operator Upgrade Note

Audience: node operators and SREs  
Purpose: quick upgrade summary for maintenance windows.

## What Changed in v2

1. Consensus activation + guardrails
- Soft-fork gate: `berz_softfork_bip34_strict`
- Hard-fork gate: `berz_hardfork_tx_v2`
- Startup guardrails block old consensus version after hard-fork activation height.

2. Mempool policy and resilience
- Stronger package/ancestor/descendant bounds.
- Rolling min-fee floor and deterministic eviction behavior under pressure.
- Persistent mempool snapshot restore with validation on restart.

3. P2P/network hardening
- Stronger connection management and bootstrap safety checks.
- Ban/reputation persistence and expanded chaos/fuzz testing coverage.

4. Wallet safety improvements
- Reduced secret exposure defaults.
- Encryption/backup and signing hardening improvements behind existing RPC shape.

## Required Operator Actions

1. Upgrade all block-producing and public-facing nodes to v2 binaries.
2. Set/verify consensus activation config:
```ini
[main]
activation_height_berz_softfork_bip34_strict = <HEIGHT>
activation_height_berz_hardfork_tx_v2 = <HEIGHT>
node_consensus_version = 2
enforce_hardfork_guardrails = true
```
3. Verify peer discovery is configured (non-regtest):
- one of: `connect`, `addnode`, `bootstrap_file`, or `dnsseed` defaults.
4. Confirm mempool defaults/overrides using:
- `get_mempool_info`
- `get_mempool_diagnostics`
5. Post-upgrade checks:
- `GET /health` is healthy
- `GET /ready` is true
- `get_block_count` / `get_best_block_hash` converge with peer quorum

## Rollback Steps

## A) Before hard-fork activation (safe rollback window)
1. Stop v2 rollout.
2. Move activation heights forward in config on all block producers.
3. Restart nodes and verify convergence.
4. If required, roll back binaries to previous stable release after activation heights are moved.

## B) After hard-fork activation (do not roll back to pre-v2 binaries)
1. Keep consensus boundary intact; do not run old binaries.
2. Trigger incident procedure and coordinate controlled restart window.
3. Deploy patched v2.x build.
4. Restore from latest healthy datadir snapshot only if local corruption is detected.
5. Rejoin network and verify tip convergence + mempool health.

## Quick References
- `docs/RUNBOOK_CONSENSUS_ACTIVATION.md`
- `docs/RUNBOOK_UPGRADE.md`
- `docs/RUNBOOK_INCIDENTS.md`
- `docs/V2_DEFAULTS_CHECKLIST.md`

