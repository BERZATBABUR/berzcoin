# BerzCoin Quick Start

## Easy mining in a few minutes (regtest)

Background CPU mining (`setgenerate`, `generate`) is available on **regtest** only. Testnet and mainnet nodes sync with the network but do not run this tutorial miner—use regtest for local blocks and wallet experiments.

### Fastest path: `easy_mine` script

From the repository root:

```bash
pip install -e ".[dev]"
chmod +x scripts/easy_mine.sh
./scripts/easy_mine.sh
```

This creates a datadir, writes a minimal config, starts the node, activates a private-key wallet, picks a coinbase address, and starts one mining thread.

---

## Manual setup (default datadir)

The CLI loads the RPC cookie from `~/.berzcoin/.cookie` when you do not pass `-rpcpassword`. Use the same datadir for the node and keep `rpcbind` / `rpcallowip` consistent with where you run `berzcoin-cli`.

### 1. Install

```bash
# From a clone of the repo:
pip install -e ".[dev]"

# If the project is published to PyPI:
# pip install berzcoin
```

### 2. Minimal config

Create `~/.berzcoin/berzcoin.conf` with a `[main]` section (required). Example:

```ini
[main]
network = regtest
datadir = ~/.berzcoin
wallet = default
wallet_private_key =
rpcbind = 127.0.0.1
rpcallowip = 127.0.0.1
```

Adjust `datadir` to an absolute path if you prefer.

### 3. Start the node

```bash
berzcoind --regtest -datadir ~/.berzcoin -conf ~/.berzcoin/berzcoin.conf
```

Leave this terminal running.

### 4. Wallet, mining address, and mining

In a second terminal (same machine; default RPC is `127.0.0.1:8332` unless you set `rpcport` in the config):

```bash
berzcoin-cli activatewallet "<private_key_hex>"
ADDR=$(berzcoin-cli getnewaddress)
berzcoin-cli setminingaddress "$ADDR"
berzcoin-cli setgenerate true --threads 2
```

If you use a non-default `rpcport` (as in `easy_mine.sh`), add `-rpcport 18443` to every `berzcoin-cli` line.

### 5. Check progress

```bash
berzcoin-cli getminingstatus
berzcoin-cli getblockcount
```

### Stop mining

```bash
berzcoin-cli setgenerate false
```

---

## Testnet vs regtest

| Mode        | Purpose                         | This repo’s CPU `setgenerate` |
|---------|---------------------------------|-------------------------------|
| `--regtest` | Private chain, instant blocks | Supported                     |
| `--testnet` | Public test network             | Not available (sync only)    |

For more RPC examples, see the main [README.md](../README.md).

---

## Wallet only (about five minutes)

The **`berzcoin-wallet`** command (from `pip install` / `-e .`) uses your **full node’s JSON-RPC** for balance, new addresses, and sends.

Prebuilt binaries (single-file download) are not published yet—you can use the Python entry point from this repo or after install:

```bash
# After: pip install -e "."

# 1. Optional: download a future standalone build (placeholder)
# wget -O berzcoin-wallet https://berzcoin.org/download/wallet
# chmod +x berzcoin-wallet

# From source / package:
# berzcoin-wallet  ===  python3 -m cli.wallet_standalone

# 2. Create local wallet material (private-key wallet JSON)
berzcoin-wallet create --network mainnet

# 3. New receiving address from the *node’s* wallet (node must be running; RPC cookie auth)
berzcoin-wallet address --node 127.0.0.1:8332

# 4. Send / balance (node must be running, wallet activated on the node)
berzcoin-wallet send --to "bc1..." --amount 10 --node 127.0.0.1:8332
berzcoin-wallet balance --node 127.0.0.1:8332
```

Use `--rpc-cookie-file /path/to/.cookie` or `--rpc-password` if RPC is not the default `~/.berzcoin/.cookie`. **`address`**, **`send`**, and **`balance`** require a running **`berzcoind`** with an active private-key wallet; only **`create`** is fully local.

---

## Run a public-facing node

`berzcoind` does not implement `-daemon`; run it under **systemd**, **tmux**, or `nohup`, and use a proper config.

### Mainnet bootstrap assistant (recommended)

Use one command to generate `berzcoin.conf` and bootstrap peers in your datadir:

```bash
python scripts/mainnet_bootstrap_assistant.py \
  --datadir ~/.berzcoin-mainnet-a \
  --bootstrap-from-seeds seed-a.example.org,seed-b.example.org \
  --probe-timeout-secs 2.0 \
  --require-reachable \
  --addnode 203.0.113.10:8333 \
  --dnsseed --dnsseeds seed-a.example.org,seed-b.example.org
```

Then start:

```bash
python -m node.app.main -conf ~/.berzcoin-mainnet-a/berzcoin.conf
```

If you already have a vetted bootstrap file:

```bash
python scripts/mainnet_bootstrap_assistant.py \
  --datadir ~/.berzcoin-mainnet-a \
  --bootstrap-source ./configs/bootstrap_nodes.json \
  --addnode 203.0.113.10:8333
```

The node reads **INI** (`ConfigParser`). `configs/mainnet_seeds.toml` is now INI-compatible and can be copied directly, but you must replace example seed hosts with real reachable hosts before production.
Note: DNS seeding is disabled by default in `configs/mainnet.toml` to avoid silent startup with placeholder peers. Enable `dnsseed = true` only after setting real `dnsseeds`, or provide a valid `bootstrap_nodes.json` in your datadir.

```bash
# 1. Example: use a seed profile and then replace dnsseeds with your real hosts
cp configs/mainnet_seeds.toml ~/.berzcoin/berzcoin.conf

# 2. Start (foreground example; use your process manager for production)
berzcoind -datadir ~/.berzcoin -conf ~/.berzcoin/berzcoin.conf

# 3. Seed operator checklist / refresh bootstrap peers list
chmod +x scripts/register_seed.sh
./scripts/register_seed.sh
```

See comments in `configs/mainnet_seeds.toml` for how DNS seeds relate to `configs/bootstrap_nodes.json` and `scripts/update_dns_seeds.py`.

---

## One-click install script

From the repository:

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

This installs the package (from the repo or PyPI when available) and prints exact next steps (start node, then wallet RPC).

---

## Alternative: chain-only node (no wallet)

The node reads **INI** (`[main]`), not TOML. To skip wallet initialization:

```ini
[main]
network = regtest
disablewallet = true
datadir = ~/.berzcoin

bind = 127.0.0.1
port = 18444
rpcbind = 127.0.0.1
rpcport = 18443
rpcallowip = 127.0.0.1
```

Start (either is valid):

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
berzcoind --regtest --disablewallet -datadir ~/.berzcoin
```

## Start node correctly

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
# equivalent network flag if not in file:
berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf
```

`berzcoin-cli` does **not** load RPC settings from `-conf`; pass **`-datadir`** (for the cookie) and **`-rpcport`** to match the node.

---

## Common issues

### Wallet activation errors

Use `createwallet` once, then activate with `activatewallet "<private_key_hex>"`.

### SQLite “database is locked”

Stop **all** processes using that datadir. With **WAL** mode, SQLite also uses sidecar files:

- `regtest.db-wal`
- `regtest.db-shm`

(For **mainnet** the base file is `mainnet.db`.) After a clean shutdown you should not delete these manually; if the node crashed and the DB will not open, **back up** the datadir, then remove the `-wal`/`-shm` files **only** with no process holding the DB.

### Regtest mining / balance

Use **`berzcoin-cli -datadir ~/.berzcoin -rpcport 18443`** (adjust port to your config). Examples:

```bash
berzcoin-cli -datadir ~/.berzcoin -rpcport 18443 activatewallet "<private_key_hex>"
berzcoin-cli -datadir ~/.berzcoin -rpcport 18443 generate 101
berzcoin-cli -datadir ~/.berzcoin -rpcport 18443 getbalance
```

Background mining: **`setgenerate true --threads 2`** (regtest only).

---

## Automated regtest profile

From the repository:

```bash
chmod +x scripts/setup_regtest.sh
./scripts/setup_regtest.sh
```

This writes `berzcoin.conf`, creates **`wallets/default`** with a valid **bcrt1** mining address (not a random fake string), and prints **`berzcoin-cli`** commands using **`-datadir`** and **`-rpcport 18443`**.

## Two-node propagation smoke test

From the repository:

```bash
chmod +x scripts/two_node_regtest_propagation.sh
./scripts/two_node_regtest_propagation.sh
```

This spins up two local regtest nodes, mines on node1, and verifies node2 reaches the same tip.
