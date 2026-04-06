# BerzCoin Consensus Activation Runbook (v2 Wave)

## Goal
Roll out the v2 consensus changes safely across networks:
- soft-fork gate: `berz_softfork_bip34_strict`
- hard-fork gate: `berz_hardfork_tx_v2`

This runbook defines final activation heights, upgrade deadlines, expected mixed-version behavior, and rollback actions.

## Final Activation Schedule

All times are UTC.

| Network | Soft Fork Height (`berz_softfork_bip34_strict`) | Hard Fork Height (`berz_hardfork_tx_v2`) | Upgrade Deadline (absolute) |
|---|---:|---:|---|
| `regtest` | `102` | `103` | N/A (dev/test only) |
| `testnet` | `120000` | `120100` | June 15, 2026 00:00:00 UTC |
| `mainnet` | `180000` | `180100` | July 15, 2026 00:00:00 UTC |

## Required Node Settings

Set these in `berzcoin.conf` (or equivalent deployment config):

```ini
[main]
activation_height_berz_softfork_bip34_strict = 180000
activation_height_berz_hardfork_tx_v2 = 180100
node_consensus_version = 2
enforce_hardfork_guardrails = true
```

These values must be pinned on every mainnet node (no per-node drift) before the
mainnet upgrade deadline: **July 15, 2026 00:00:00 UTC**.

You can override from CLI if needed:

```bash
python -m node.app.main \
  --activation-height berz_softfork_bip34_strict=180000 \
  --activation-height berz_hardfork_tx_v2=180100
```

## Expected Behavior

## Before Soft Fork Height
- Legacy and upgraded nodes should accept the same blocks.
- Coinbase height checks remain on the legacy relaxed path.

## At/After Soft Fork Height
- Strict BIP34-style encoding is enforced (`berz_softfork_bip34_strict`).
- Blocks with non-minimal/misplaced coinbase-height encoding are rejected by upgraded nodes.

## At/After Hard Fork Height
- Transactions with `version < 2` are rejected by consensus.
- Mempool rejects txs that would be invalid for the next block.
- Miner/template selection excludes txs invalid under active consensus.
- Nodes with `node_consensus_version < 2` are blocked at startup by guardrail once tip is at or above hard-fork height.

Legacy alias names (`softfork_strict_bip34`, `hardfork_v2_tx_version`) are still accepted for backward compatibility, but new configs should use canonical names.

## Mixed-Version Network Expectations
- Pre-hard-fork: mixed peers may coexist.
- Post-hard-fork: old binaries are expected to diverge or fail guardrail checks and must not be considered healthy participants.

## Rollout Procedure

1. T-14 days:
- Publish activation notice with the exact heights and UTC deadline.
- Release v2 binaries and tag release SHA.

2. T-7 days:
- Confirm >95% of block-producing nodes report `node_consensus_version=2`.
- Run multi-node activation drills on testnet/regtest.

3. T-24 hours:
- Freeze consensus-related merges.
- Reconfirm all production configs include both activation heights.

4. Activation window:
- Monitor `get_best_block_hash`, peer height spread, and reorg frequency.
- Track mempool rejection reasons for `consensus_tx_version_too_low`.

5. T+24 hours:
- Confirm chain convergence and normal reorg baseline.
- Publish post-activation status update.

## Pre-Activation Rollback Plan (Safe)

If a critical issue is found before hard-fork height:

1. Stop rollout and pause new binary promotion.
2. Raise both activation heights in config to a future value on all block-producing nodes.
3. Restart nodes with updated config.
4. Announce revised schedule with a new absolute deadline.

This is the preferred rollback path.

## Post-Activation Incident Plan (Hard Fork Active)

If a critical issue is found after hard-fork height is crossed:

1. Do not roll back to pre-v2 binaries.
2. Trigger incident protocol (`docs/RUNBOOK_INCIDENTS.md`) and coordinate a controlled halt/restart window.
3. Ship patched v2.x release that preserves the active hard-fork rule boundary.
4. Recover nodes from latest healthy datadir snapshot if local corruption occurred.
5. Rejoin network and verify tip convergence.

## Validation Checklist

- `GET /health` is `ok`.
- `GET /ready` is `true`.
- `getblockcount` and `getbestblockhash` converge across quorum nodes.
- `getrawmempool` does not retain txs rejected by active consensus.
- No sustained abnormal reorg depth after activation.

## References
- `docs/release-process.md`
- `docs/RUNBOOK_UPGRADE.md`
- `docs/RUNBOOK_INCIDENTS.md`
- `shared/consensus/rules.py`
- `node/chain/validation.py`
- `node/mempool/pool.py`
