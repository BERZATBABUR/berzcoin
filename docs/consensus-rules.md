# BerzCoin Consensus Rules (Current v0.1)

This document summarizes consensus-critical behavior currently implemented in code.

Source of truth:
- `shared/consensus/params.py`
- `shared/consensus/rules.py`
- `shared/consensus/pow.py`
- `node/chain/validation.py`

## Monetary and Unit Rules

- Base unit: `1 BERZ = 100,000,000 sat`.
- Max nominal supply parameter: `2^24 BERZ` (via `MAX_MONEY` in sat units).
- Monetary validity checks must use `params.max_money` (not hardcoded BTC constants).

## Subsidy and Halving

- Subsidy is computed by `get_block_subsidy(height, params)`.
- Halving interval is derived for approximately 4 years from block spacing.
- Subsidy + fees defines the maximum allowed coinbase output value.

## Proof of Work

- Validity condition: `hash(block_header) <= target(bits)`.
- Difficulty retarget uses the configured spacing/timespan and 4x clamp behavior.
- Current defaults in params:
  - Target spacing: 120 seconds (2 minutes).
  - Retarget interval from `pow_target_timespan / pow_target_spacing`.

## Transaction Validity (Summary)

A transaction is rejected if any of the following fail:
- Referenced inputs missing or already spent.
- Signature/script verification fails for spend path.
- Output values invalid (negative or overflow).
- Total output exceeds total input.
- Duplicate input outpoints in the same transaction.

## Block Validity (Summary)

A block is rejected if any of the following fail:
- Header/PoW invalid.
- Parent linkage invalid.
- Merkle root mismatch.
- Coinbase placement/reward constraints invalid.
- Any included transaction invalid under stateful UTXO validation.

## Reorg Rules

- Heavier branch can trigger reorg.
- Reorg manager enforces safety depth (`max_reorg_depth`).
- Disconnect and reconnect are done with rollback protections.

## Checkpoints

- Code-level checkpoint map is in `ConsensusParams.checkpoint_data`.
- Operator JSON checkpoint files in `genesis/` are documentation artifacts today unless explicitly wired into active runtime logic.

## Important Limitations

For "Bitcoin-like" goals, these remain incomplete:
- Full script/tapscript historical consensus quirks.
- Full BIP341/342 edge-case parity.
- Full differential test corpus against Bitcoin Core behavior.
