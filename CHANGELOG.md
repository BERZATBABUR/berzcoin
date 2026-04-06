# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic versioning.

## [Unreleased]

### Changed

- Tightened CI quality gates so lint and security checks can fail builds.
- Added v1 sharing/run playbook and release smoke-test script.

## [2.0.0] - 2026-04-06

v2 mainnet rollout release.

### Release Notes (RC -> Final)

- RC->release default deltas are present and documented (not a "no-delta" release).
- Network defaults:
  - `dnsseed` default is `true`.
  - deterministic bootstrap/discovery safety checks are enforced for non-regtest unless `allow_missing_bootstrap=true`.
- Consensus safety defaults:
  - `node_consensus_version = 2`
  - `enforce_hardfork_guardrails = true`
  - support for `custom_activation_heights` (`activation_height_*` config keys).
- Mempool defaults and compatibility:
  - explicit v2 mempool policy/limit defaults are exposed (`mempool_min_relay_fee`, rolling floor, package/ancestor/descendant limits).
  - legacy knobs remain compatible (`mempoolminfee`, `maxmempool`).
- Wallet security defaults:
  - `wallet_debug_secrets = false`
  - `wallet_encryption_passphrase = ""`
  - `wallet_default_unlock_timeout = 300`
- Mainnet operator templates/runbooks are pinned to v2 activation values:
  - `activation_height_berz_softfork_bip34_strict = 180000`
  - `activation_height_berz_hardfork_tx_v2 = 180100`
  - upgrade deadline: **July 15, 2026 00:00:00 UTC**.

## [0.1.0] - 2026-03-28

First public v1 baseline.

### Added

- Validation-first full node, RPC server, and CLI tools (`berzcoind`, `berzcoin-cli`, `berzcoin-wallet`).
- Private-key wallet activation model (`createwallet`, `activatewallet`, wallet send/receive flows).
- Regtest mining controls (manual and background), mining dashboard pages, and one-command launcher:
  - `scripts/run_v1_interface.sh`
- Operational docs and runbooks:
  - `docs/QUICK_START.md`
  - `docs/PRODUCTION_OPS.md`
  - `docs/RUNBOOK_DEPLOY.md`
  - `docs/RUNBOOK_UPGRADE.md`
  - `docs/RUNBOOK_INCIDENTS.md`
- Release and packaging helpers:
  - `scripts/build_linux_packages.sh`
  - `scripts/check_no_placeholder_artifacts.sh`

### Changed

- v1 launcher defaults tuned for faster local demo UX:
  - block target defaults to 60s
  - coinbase maturity configurable and set low for local v1 flow
  - mining address can be managed without forcing active-wallet address matching in v1 launcher config

### Removed

- Lightwallet/stratum surfaces from v0.1 scope.

### Known Limits (v1)

- Regtest is the supported environment for local mining/send demos.
- Public-network mining economics/security are not Bitcoin Core parity yet.
- No standalone GUI desktop package in v1; dashboard is web-based.
