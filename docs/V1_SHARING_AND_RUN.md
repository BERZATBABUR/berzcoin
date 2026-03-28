# BerzCoin v1: Sharing Checklist and Run Guide

This document is for two goals:

1. What to finish before sharing a first public v1 build.
2. Exact steps for another developer to run BerzCoin in a fresh environment.

## 1) What Is Left Before Sharing v1

Use this as a release-readiness checklist.

### P0 (must-do before sharing publicly)

- Freeze scope for v1:
  - private-key wallet model
  - regtest-first mining/demo flow
  - dashboard + CLI as the primary interface
- Run release test gates and save results:
  - `pytest tests/unit -v`
  - `pytest tests/integration -v`
  - `pytest tests/e2e -v`
- Run artifact safety check:
  - `bash scripts/check_no_placeholder_artifacts.sh`
- Build release artifacts:
  - `python -m build`
  - `bash scripts/build_linux_packages.sh` (if `.deb` / `.rpm` are part of v1 delivery)
- Validate launcher experience on a clean datadir:
  - `scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1`
- Confirm docs match current behavior:
  - mining can keep running across wallet activation changes
  - v1 coinbase maturity is configurable from launcher (`--coinbase-maturity`, default `1`)
  - dynamic-port behavior uses `~/.berzcoin_v1/run_info.env`
- Prepare release notes:
  - wallet behavior changes
  - mining behavior changes
  - known limitations (regtest-focused miner, no lightwallet/stratum)

### P1 (highly recommended right after first share)

- Keep lint/security checks blocking in CI (do not relax back to soft-fail mode).
- Add a short changelog file (`CHANGELOG.md`) linked from `README.md`.
- Keep a scripted smoke test in release checklist:
  - `scripts/v1_release_smoke.sh`

### P2 (nice-to-have for smoother adoption)

- Publish a Docker quick-start section linked from `README.md`.
- Publish checksums/signatures for built artifacts.
- Add a small “known issues” section for common regtest/demo pitfalls.

## 2) Run BerzCoin v1 In a Fresh Environment

These steps are for Linux/macOS shell users.

### Prerequisites

- Python `3.10+`
- `git`
- `pip`

### Install from source

```bash
git clone <YOUR_REPO_URL>
cd BerzCoin
python3 -m pip install -U pip
pip install -e ".[dev]"
```

### Start v1 interface (recommended)

```bash
cd /path/to/BerzCoin
scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1
```

The launcher prints the dashboard URL and writes runtime info to:

- `~/.berzcoin_v1/run_info.env`

Note:
- v1 launcher resets datadir by default for a fresh chain.
- Add `--no-reset-datadir` if you want to keep existing chain state.

Use:

```bash
source ~/.berzcoin_v1/run_info.env
echo "$DASHBOARD_URL"
```

Open that URL in your browser.

### First actions in dashboard

1. Go to **Wallet** and activate wallet using your private key (or create one).
2. Go to **Mining** and set a reward address.
3. Click **Start Mining**.
4. Mine at least 1 block, then send BERZ from Wallet page.

Note:
- v1 launcher default is `coinbase_maturity=1`, so spending mined coinbase rewards usually needs only one confirmation.
- If you want immediate spend for local demo only:
  - `scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1 --coinbase-maturity 0`

### Stop and restart

Stop:

```bash
kill "$(cat ~/.berzcoin_v1/node.pid)"
```

Restart without wiping data:

```bash
scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1
```

### Troubleshooting

- If ports are busy, launcher auto-selects free ports. Always use `run_info.env` values.
- If reset fails because files are busy, stop existing node first using `node.pid`, then rerun with `--reset-datadir`.
- If mining does not start, ensure a valid mining address is set in Mining page.
- If transactions do not confirm, ensure mining is active and check latest block height on the Blocks page.
