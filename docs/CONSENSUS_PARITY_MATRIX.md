# Consensus Parity Matrix (Step 1 Baseline)

Date: 2026-04-25  
Goal: Establish a code-backed baseline of what is already Bitcoin-like, what is partial, and what is missing for consensus parity.

Status legend:
- `Implemented`: behavior is present and appears intentionally enforced.
- `Partial`: behavior exists but is simplified, always-on when it should be activation-gated, or missing edge-case coverage.
- `Missing`: no clear enforcement path found in active validation flow.

## Activation and Deployment Semantics

| Area / BIP | Status | Evidence | Gap to Bitcoin parity |
|---|---|---|---|
| Buried deployments by height (`bip34`, `bip65`, `bip66`, `csv`, `segwit`) | Implemented | `shared/consensus/buried_deployments.py` | Height map exists, but downstream rule enforcement is uneven across BIPs. |
| BIP9 versionbits state machine | Partial | `shared/consensus/versionbits.py`, `node/chain/chainstate.py` | Simplified tracking; does not model Bitcoin Core period-by-period state transitions exactly. |
| Unified activation checks (buried + custom gates) | Partial | `shared/consensus/buried_deployments.py` | Custom deployment and versionbits integration is project-specific and not Bitcoin deployment semantics. |

## Block and Transaction Consensus

| Area / BIP | Status | Evidence | Gap to Bitcoin parity |
|---|---|---|---|
| BIP34 coinbase height commitment | Partial | `node/chain/validation.py`, `shared/consensus/rules.py` | Basic height encoding checks exist; strict mode is custom-gated (`berz_softfork_bip34_strict`) rather than exact Bitcoin historical behavior. |
| BIP30 duplicate txid overwrite prevention | Missing | No explicit chain-level BIP30 rule in `node/chain/validation.py` / `shared/consensus/rules.py` | Bitcoin has specific duplicate-txid protections for historical edge cases. |
| BIP66 strict DER signatures | Partial | `shared/script/sigchecks.py`, flags in `shared/script/script_flags.py` | DER checks exist but appear effectively always enabled via default flags, not height-accurate historical activation. |
| BIP65 CLTV | Partial | opcode handling in `shared/script/engine.py` | CLTV opcode exists, but semantics are simplified and activation handling is not Bitcoin-historical. |
| BIP68 relative lock-time | Missing (wired path) | helper exists in `shared/consensus/sequence_locks.py` | Sequence lock module is present but not integrated into block/tx acceptance path. |
| BIP112 CSV | Partial | opcode handling in `shared/script/engine.py` | CSV exists but with simplified checks and no full BIP68/113 context integration. |
| BIP113 median-time-past for tx finality | Missing (wired path) | `shared/consensus/locktime.py` exists, but no active use in `node/chain/validation.py` tx acceptance | MTP is used for block timestamp checks, but transaction finality path is not clearly enforced like Bitcoin. |

## SegWit and Taproot Consensus

| Area / BIP | Status | Evidence | Gap to Bitcoin parity |
|---|---|---|---|
| BIP141 SegWit tx format / witness-aware txid-wtxid | Partial | `shared/core/transaction.py`, `shared/script/verify.py` | Witness spending paths exist, but no clear block-level witness commitment validation in coinbase. |
| BIP143 SegWit v0 sighash | Implemented (core path) | `shared/script/sigchecks.py`, used from `shared/script/verify.py` | Needs broader vector/differential coverage to claim parity confidence. |
| BIP147 NULLDUMMY | Partial | `shared/script/engine.py`, `shared/script/script_flags.py` | NULLDUMMY check exists, but activation/historical rule timing does not appear Bitcoin-accurate. |
| BIP340 Schnorr | Partial | `shared/crypto/secp256k1.py`, `shared/script/sigchecks.py` | Core primitives exist, but parity requires exhaustive edge-case vectors and consensus-failure equivalence. |
| BIP341 Taproot key-path | Partial | `shared/script/verify.py`, `shared/script/sigchecks.py`, `shared/script/tapscript.py` | Implemented with simplifications; full parity (annex/sighash/script tree edge cases) not demonstrated. |
| BIP342 Tapscript | Partial | `shared/script/tapscript.py` | Executor exists, but project docs already mark BIP341/342 edge-case parity as incomplete. |

## Cross-Cutting Consensus Validation Quality

| Area | Status | Evidence | Gap to Bitcoin parity |
|---|---|---|---|
| Differential testing vs Bitcoin Core | Partial | `tests/integration/test_bitcoin_core_differential.py` | Current differential scope is small (script-type decode + txid/wtxid); not a full consensus corpus. |
| Consensus docs acknowledge remaining gaps | Implemented (honest baseline) | `docs/consensus-rules.md`, `docs/architecture.md` | Good transparency, but still requires implementation + vectors to close parity. |

## Notes on Intentional Network Divergence

These are not necessarily "bugs", but they are not Bitcoin parity:
- BerzCoin network/genesis/monetary/spacing profile differs (`shared/consensus/params.py`).
- Custom deployment names and activation controls (`berz_softfork_bip34_strict`, `berz_hardfork_tx_v2`) are project-specific.

## Immediate Next Outputs for Step 2

1. Convert each `Partial`/`Missing` line above into a tracked implementation ticket with acceptance tests.
2. Prioritize activation correctness first (BIP9 + buried/historical gating), then locktime/sequence semantics, then witness/taproot edge cases.
3. Expand differential harness from 2 checks to a curated consensus vector suite.
