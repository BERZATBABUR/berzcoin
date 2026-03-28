# BerzCoin

BerzCoin full node, wallet CLI, and shared libraries (Python).

## Scope (v0.1)

- Canonical private-key wallet model only
- Validation-first full node
- Minimal RPC/CLI/dashboard surface
- Lightwallet and Stratum components are removed from this scope

## Quick start

```bash
export PYTHONPATH="$(pwd)"
python3 -m node.app.main -datadir ~/.berzcoin
```

Or install in editable mode: `pip install -e ".[dev]"` then run `berzcoind -datadir ~/.berzcoin`.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/unit tests/integration -v
```

Optional Bitcoin Core differential checks (external dependencies required):

```bash
scripts/core_diff_preflight.sh
BERZ_ENABLE_CORE_DIFF=1 BERZ_REQUIRE_CORE_DIFF=1 pytest -q tests/integration/test_bitcoin_core_differential.py -rs
```

## Release Artifacts

- Python sdist/wheel:
  - `python -m build`
- Linux packages (`.deb`, `.rpm`, requires `fpm` + `rpmbuild`):
  - `scripts/build_linux_packages.sh`
- Sanity check to prevent placeholder package files in git:
  - `scripts/check_no_placeholder_artifacts.sh`
- v1 release smoke check:
  - `scripts/v1_release_smoke.sh`
- Release notes:
  - `CHANGELOG.md`

## Usage examples (CLI)

Run `berzcoin-cli` with `-rpcconnect`, `-rpcport`, `-rpcuser`, `-rpcpassword` if your node differs from defaults (`127.0.0.1:8332`, user `berzcoin`).

### Mining control

```bash
# Start mining (regtest); use --threads for worker count
berzcoin-cli setgenerate true --threads 4

# Stop mining
berzcoin-cli setgenerate false

# Mining status
berzcoin-cli getminingstatus

# Mining reward address
berzcoin-cli setminingaddress "bcrt1q..."
```

### Wallet control

```bash
# Create wallet (name argument kept for CLI compatibility)
berzcoin-cli createwallet mywallet

# Activate an existing wallet from private key
berzcoin-cli loadwallet "<private_key_hex>"
berzcoin-cli activatewallet "<private_key_hex>"

# List known wallet addresses from datadir/wallets/
berzcoin-cli listwallets
```

### Web dashboard (optional)

Enable in config: `webdashboard=true`, `webhost=127.0.0.1`, `webport=8080`, then open `http://127.0.0.1:8080/`.

For local demos:

```bash
scripts/dashboard_demo.sh --help
scripts/dashboard_demo.sh --reset-datadir   # optional, destructive
```

One-command v1 launcher (background):

```bash
scripts/run_v1_interface.sh
```

This starts node + dashboard only (neutral mode) and verifies `http://127.0.0.1:<webport>/` is reachable.
By default, v1 launcher resets the datadir so the chain starts fresh each run.
No wallet is auto-activated and no mining address is preloaded; activate with your private key from the Wallet page.
Use `--no-reset-datadir` to keep existing chain state.

Optional demo bootstrap (auto wallet+mining):

```bash
scripts/run_v1_interface.sh --bootstrap-demo
```

Mainnet mode (safe defaults: no auto reset, no demo mining bootstrap):

```bash
scripts/run_v1_interface.sh --network mainnet --datadir ~/.berzcoin_v1_mainnet
```

When default ports are busy, the script auto-selects free ports and writes them to `~/.berzcoin_v1/run_info.env`.

LAN peer mode (two computers on same network):

```bash
# Node A (miner): expose P2P on LAN
scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1 --lan-mode --p2p-port 18444 --no-reset-datadir

# Node B: connect to Node A
scripts/run_v1_interface.sh --datadir ~/.berzcoin_v1 --lan-mode --p2p-port 18444 --addnode <NODE_A_LAN_IP>:18444 --no-reset-datadir
```

Keep RPC/dashboard local; only P2P is opened by `--lan-mode`.

### Quick start: dashboard + wallet + regtest mining

The node reads **INI**-style config (`ConfigParser`), not TOML. Use a **`[main]`** section (or any section name; keys are merged into the flat config).

Add or merge into `~/.berzcoin/berzcoin.conf`:

```ini
[main]
network = regtest
datadir = ~/.berzcoin

# Web dashboard (same process as the node)
webdashboard = true
webhost = 127.0.0.1
webport = 8080

# Wallet (private-key activation model)
wallet = default
wallet_private_key =

# Regtest CPU mining — set miningaddress to a real regtest address before enabling autostart
mining = true
miningaddress = bcrt1qyour_mining_address_here
autominer = true
```

Notes:

- **`miningaddress`** must be a valid regtest address. If the wallet is new, start once with `mining = false` and `autominer = false`, then run `berzcoin-cli activatewallet "<private_key_hex>"`, `berzcoin-cli getnewaddress`, `berzcoin-cli setminingaddress "<address>"`, set `miningaddress` in the conf (or rely on RPC), and set `mining` / `autominer` as needed; restart if you only changed the file.
- Dashboard **Start/Stop mining** buttons are **regtest-only**; use `network = regtest` or start with **`berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf`**.
- Mining autostart needs an active wallet identity; activate via **`berzcoin-cli activatewallet "<private_key_hex>"`** if the miner refuses to start.

Start the node:

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
# or explicitly:
berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf
```

Then open **http://127.0.0.1:8080/**.

More detail: [docs/QUICK_START.md](docs/QUICK_START.md).

Release-readiness checklist + fresh-environment run guide:
[docs/V1_SHARING_AND_RUN.md](docs/V1_SHARING_AND_RUN.md).

## Ops and production readiness

- Health/readiness/metrics endpoints:
  - `GET /health`
  - `GET /ready`
  - `GET /metrics`
  - `GET /metrics/prometheus`
- Alert rules: `ops/prometheus/alerts.yml`
- Grafana starter dashboard: `ops/grafana/berzcoin-node-dashboard.json`
- Runbooks:
  - `docs/RUNBOOK_DEPLOY.md`
  - `docs/RUNBOOK_UPGRADE.md`
  - `docs/RUNBOOK_INCIDENTS.md`

Useful tuning keys in `berzcoin.conf`:
- `sync_getdata_batch_size`
- `sync_poll_interval_secs`
- `sync_block_request_timeout_secs`
- `sync_error_backoff_secs`
- `blocks_cache_size`

Optional trust-chain admission (P2P):
- `authority_chain_enabled = true`
- `authority_trusted_nodes = 203.0.113.10,198.51.100.7`

When enabled, trusted/verified nodes can verify newly joining nodes, and newly verified nodes become verifiers for the next joins.

## License

See LICENSE.
