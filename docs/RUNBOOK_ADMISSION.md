# Admission Runbook

This runbook covers strict node admission and rejoin behavior.

## Recommended Mainnet Defaults

Use strict admission:

```ini
[main]
authority_chain_enabled = true
admission_mode = strict
min_verifier_votes = 2
```

## Bootstrap Checklist

1. Start at least two trusted verifier nodes.
2. Configure `authority_trusted_nodes` on bootstrap nodes.
3. Start seed registry service.
4. Join new nodes with:
   - `berzcoin-cli joinnetwork --seed-registry http://<ip>:8787 --self-ip <this-ip> --port 8333`

## Rejoin Continuity

- Each node persists `node_identity.json` in datadir.
- Rejoin identity is validated against historical `node_id -> pubkey`.
- If a known `node_id` presents a different pubkey, admission is rejected (`rejoin_identity_mismatch`).

## Readiness Gate

- A node behind tip can connect and sync.
- While behind, it must not verify new nodes (`verifier_not_synced`).
- Verifier role resumes only when sync lag is small.

## Common Rejection Reasons

- `no_trusted_verifier_available`
- `verifier_not_synced`
- `challenge_expired`
- `attestation_time_skew`
- `invalid_attestation_signature`
- `attestation_replay`
- `rejoin_identity_mismatch`

Inspect via:

- `berzcoin-cli getnetworkinfo`
- dashboard `/api/authority/chain`

## Emergency Fallback

For incident recovery only (temporary):

```ini
[main]
admission_mode = assisted
min_verifier_votes = 1
```

After recovery, switch back to strict and restart node.

