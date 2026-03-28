# BerzCoin production operations (reference)

This is an **operator checklist** for running `berzcoind` on a server. Adjust paths, users, and firewall rules for your environment.

## Preconditions

- **Python 3.10+**, dependencies from `pyproject.toml`
- **Disk**: enough space for `datadir` and SQLite (`<network>.db`) plus blocks
- **Network**: open the **P2P** port you configure (`port`, default **8333**) if you want inbound peers

## Install

```bash
cd /path/to/BerzCoin
pip install -e ".[dev]"
```

## Configuration (INI)

`berzcoind` uses **INI** (`[main]`), not TOML. Example:

```ini
[main]
network = mainnet
datadir = /var/lib/berzcoin

bind = 0.0.0.0
port = 8333

rpcbind = 127.0.0.1
rpcport = 8332
rpcallowip = 127.0.0.1

dnsseed = true
dnsseeds = your.real.seed1.org,your.real.seed2.org

# Optional: comma-separated static peers
# addnode = 203.0.113.10:8333,198.51.100.2:8333

# Optional: only these peers (disables DNS-based outbound discovery)
# connect = 203.0.113.10:8333

# Optional: JSON file under datadir (see repo configs/bootstrap_nodes.json)
bootstrap_file = bootstrap_nodes.json
bootstrap_enabled = true
```

Copy or generate **`bootstrap_nodes.json`** into **`datadir`** if you use `bootstrap_enabled`. The node loads **`bootstrap_nodes`** entries into the address manager on startup.

## Process manager

`berzcoind` has **no `-daemon` flag**. Use **systemd**, **supervisor**, or **tmux**.

Example **systemd** unit (edit paths and user):

```ini
[Unit]
Description=BerzCoin full node
After=network-online.target
Wants=network-online.target

[Service]
User=berzcoin
Group=berzcoin
ExecStart=/usr/bin/python3 -m node.app.main -conf /var/lib/berzcoin/berzcoin.conf
WorkingDirectory=/opt/berzcoin
Environment=PYTHONPATH=/opt/berzcoin
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Stop via RPC when the node is running: `berzcoin-cli stop` (with correct `-datadir` / `-rpcport`).

## Security

- Keep **RPC** on **localhost** unless you know how to restrict **IP allowlists** and TLS/reverse proxies.
- Protect **`~/.berzcoin/.cookie`** (or datadir cookie): RPC auth uses it.
- **Firewall**: allow **P2P** from the internet only if you intend to accept inbound peers; **RPC** should not be exposed publicly without extra controls.

## Monitoring

- Logs: `debug.log` under datadir when configured
- RPC: `get_blockchain_info`, `get_mempool_info`, wallet methods (if wallet enabled)
- CLI: `berzcoin-cli getmempoolinfo`, `getblockchaininfo`, etc. (use **`-datadir`** and **`-rpcport`** matching the node)
- HTTP endpoints:
  - `GET /health`
  - `GET /ready`
  - `GET /metrics`
  - `GET /metrics/prometheus`

Alert and dashboard starter artifacts:

- Prometheus alerts: `ops/prometheus/alerts.yml`
- Grafana dashboard: `ops/grafana/berzcoin-node-dashboard.json`
- Runbooks:
  - `docs/RUNBOOK_DEPLOY.md`
  - `docs/RUNBOOK_UPGRADE.md`
  - `docs/RUNBOOK_INCIDENTS.md`

## Troubleshooting

- **No peers**: fix **DNS seeds**, add **`addnode`**, or place a valid **`bootstrap_nodes.json`** in **datadir**.
- **SQLite locked**: ensure only **one** process uses the same **datadir**; after a crash, see `docs/QUICK_START.md` (WAL sidecar files).
- **Regtest vs mainnet**: use **`network = regtest`** or **`--regtest`**; default RPC port may still be **8332** unless you set **`rpcport`**.

## Not in core (do not assume)

- `berzcoind -daemon`
- `berzcoin-cli vacuum` (no such RPC in this tree)
- `prometheus = true` in config (not wired unless you add it)
