# v2 RC Cleanup Plan (Reproducible Release)

Branch: `release/v2-rc-cleanup`

Goal: split current large working state into reviewable, reproducible release slices.

## PR Split by Domain

1. `consensus`
- Paths:
  - `shared/consensus/**`
  - `node/chain/**`
  - `tests/unit/test_shared/test_block_rules.py`
  - `tests/consensus/**`
- Gate:
  - consensus unit tests + activation boundary regressions.

2. `mempool`
- Paths:
  - `node/mempool/**`
  - `node/storage/mempool_store.py`
  - `node/rpc/handlers/mempool.py`
  - `cli/commands/mempool.py`
  - `tests/unit/node/test_mempool_*`
  - `tests/fuzz/test_mempool_*`
  - `tests/chaos/**mempool**`
- Gate:
  - mempool unit suite + chaos/fuzz smoke.

3. `p2p`
- Paths:
  - `node/p2p/**`
  - `shared/protocol/**`
  - `tests/unit/node/test_connman_hardening.py`
  - `tests/unit/node/test_compact_block_flow.py`
  - `tests/integration/test_reorg_activation_boundary.py`
- Gate:
  - peer/bootstrap hardening + compact block tests.

4. `wallet`
- Paths:
  - `node/wallet/**`
  - `node/rpc/handlers/wallet*.py`
  - `shared/crypto/**`
  - `tests/unit/wallet/**`
  - `tests/unit/rpc/test_wallet_*`
- Gate:
  - wallet security + backup/restore + signing tests.

5. `docs-tests-ops`
- Paths:
  - `docs/**`
  - `.github/workflows/**`
  - `scripts/release_candidate_soak.sh`
  - `scripts/generate_release_manifest.py`
  - `requirements-lock.txt`
- Gate:
  - docs consistency + CI/workflow lint + manifest generation.

## Release Reproducibility Checklist

- [ ] `requirements-lock.txt` committed and reviewed.
- [ ] CI workflows install pinned versions from lockfile.
- [ ] `python scripts/generate_release_manifest.py` produces `release/manifest.json`.
- [ ] tag points to clean commit (`git status` clean).
- [ ] rebuild from tag + lockfile reproduces identical package hashes.

