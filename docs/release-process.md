# Release Process (v0.1 Baseline)

This is the minimum release flow for this repository.

## 1. Freeze and Scope

- Freeze feature set for the release branch.
- Confirm consensus-affecting changes are explicitly called out.
- Ensure docs/config/genesis artifacts match shipped behavior.

## 2. Test Gates

Required before tagging:
- Unit tests (`tests/unit`).
- Integration tests (`tests/integration`).
- Focused reorg/recovery regressions.
- Mempool policy regressions.

Optional but recommended:
- Real-process two-node propagation checks.
- Fault-injection soak suite.
- RC soak archive gate (real CI runner) with immutable artifacts:
  - Run `RC Soak` workflow for `24h`, then `72h`.
  - Execute both seed modes (`fixed`, `rotating`).
  - Require `3` consecutive passing runs per seed mode.
  - Artifact path format:
    - `artifacts/rc-soak/<rc_tag>/<start>-to-<end>/...`
    - `artifacts/rc-soak/<rc_tag>/<start>-to-<end>.tar.gz`
- Bitcoin Core differential checks (requires external `bitcoind` + `bitcoin-cli`):
  - `scripts/core_diff_preflight.sh`
  - `BERZ_ENABLE_CORE_DIFF=1 BERZ_REQUIRE_CORE_DIFF=1 pytest -q tests/integration/test_bitcoin_core_differential.py -rs`
- Field validation at scale:
  - weekly Stage A/B/C gates (5/20/50+ node profiles)
  - adversarial scenario set + SLO window checks
  - see `docs/RUNBOOK_FIELD_VALIDATION_SCALE.md`

## 3. Artifact and Config Review

- Validate default config templates in `configs/`.
- Freeze and verify v2 defaults checklist:
  - `docs/V2_DEFAULTS_CHECKLIST.md`
- Freeze dependency/tool inputs:
  - `requirements-lock.txt`
  - pinned workflow runtime/tool versions in `.github/workflows/*.yml`
- Generate release manifest:
  - `python scripts/generate_release_manifest.py`
  - artifact: `release/manifest.json`
- Validate genesis/checkpoint files in `genesis/` are syntactically valid and documented.
- Verify runbooks and quick-start docs are consistent with RPC/API behavior.
- Ensure no placeholder binaries are committed:
  - `scripts/check_no_placeholder_artifacts.sh`
- Build distributable artifacts into `dist/` and `dist/packages/`:
  - Python: `python -m build`
  - Linux packages (fpm): `scripts/build_linux_packages.sh`
- Demo script safety:
  - `scripts/dashboard_demo.sh` does not delete datadir by default.
  - Use `--reset-datadir` only for disposable demo data.

## 4. Tagging

- Bump version metadata as needed.
- Create signed git tag.
- Publish changelog with:
  - consensus changes
  - wallet/RPC changes
  - p2p/mempool policy changes
  - migration/reindex requirements

## 5. Operator Communication

For any consensus-breaking change, release notes must include:
- exact version boundary
- whether reindex/replay is required
- compatibility expectations for mixed-version networks
- activation heights and absolute UTC upgrade deadline
- link to `docs/RUNBOOK_CONSENSUS_ACTIVATION.md`

## 6. Post-Release

- Monitor health metrics and incident channels.
- Publish hotfix timeline if a consensus or safety bug is found.

## 7. RC Soak Artifact Requirements

Each soak iteration must include:
- mempool growth (`artifacts/chaos/mempool_growth.jsonl`)
- reject histogram (`artifacts/chaos/mempool_reject_reasons.json`)
- eviction histogram (`artifacts/chaos/mempool_eviction_reasons.json`)
- peer stats (`artifacts/chaos/peer_stats.json`)
- process crash/restart trace (`process_events.jsonl`, `mempool_summary.json`, `long_run_summary.json`)
- JUnit and raw logs (`junit.xml`, `soak.log`)

Validation gate:
- `scripts/validate_soak_artifacts.py` must pass for each run.
- `scripts/assert_soak_consecutive_passes.py` must confirm last 3 runs are passing.
