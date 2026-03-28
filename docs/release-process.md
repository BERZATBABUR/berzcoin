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
- Bitcoin Core differential checks (requires external `bitcoind` + `bitcoin-cli`):
  - `scripts/core_diff_preflight.sh`
  - `BERZ_ENABLE_CORE_DIFF=1 BERZ_REQUIRE_CORE_DIFF=1 pytest -q tests/integration/test_bitcoin_core_differential.py -rs`

## 3. Artifact and Config Review

- Validate default config templates in `configs/`.
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

## 6. Post-Release

- Monitor health metrics and incident channels.
- Publish hotfix timeline if a consensus or safety bug is found.
