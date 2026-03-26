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

This creates a datadir, writes a minimal config, starts the node, unlocks the wallet, picks a coinbase address, and starts one mining thread. Watch the script output for the wallet passphrase and RPC port (defaults to `127.0.0.1:18443` in the script).

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
walletpassphrase = change-me-to-a-strong-secret
rpcbind = 127.0.0.1
rpcallowip = 127.0.0.1
```

Adjust `datadir` to an absolute path if you prefer. On first start the node creates an encrypted wallet if `wallets/default` does not exist yet.

### 3. Start the node

```bash
berzcoind --regtest -datadir ~/.berzcoin -conf ~/.berzcoin/berzcoin.conf
```

Leave this terminal running.

### 4. Wallet, mining address, and mining

In a second terminal (same machine; default RPC is `127.0.0.1:8332` unless you set `rpcport` in the config):

```bash
berzcoin-cli unlockwallet "change-me-to-a-strong-secret" --timeout 86400
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

The **`berzcoin-wallet`** command (from `pip install` / `-e .`) creates a **local encrypted wallet file** and uses your **full node’s JSON-RPC** for balance, new addresses, and sends.

Prebuilt binaries (single-file download) are not published yet—you can use the Python entry point from this repo or after install:

```bash
# After: pip install -e "."

# 1. Optional: download a future standalone build (placeholder)
# wget -O berzcoin-wallet https://berzcoin.org/download/wallet
# chmod +x berzcoin-wallet

# From source / package:
# berzcoin-wallet  ===  python3 -m cli.wallet_standalone

# 2. Create local encrypted wallet (offline file under ~/.berzcoin_wallet/)
berzcoin-wallet create --password "your-secret" --network mainnet

# 3. New receiving address from the *node’s* wallet (node must be running; RPC cookie auth)
berzcoin-wallet address --node 127.0.0.1:8332

# 4. Send / balance (node must be running, wallet unlocked on the node)
berzcoin-wallet send --to "bc1..." --amount 10 --node 127.0.0.1:8332
berzcoin-wallet balance --node 127.0.0.1:8332
```

Use `--rpc-cookie-file /path/to/.cookie` or `--rpc-password` if RPC is not the default `~/.berzcoin/.cookie`. **`address`**, **`send`**, and **`balance`** require a running **`berzcoind`** with a loaded and unlocked node wallet; only **`create`** is fully local.

---

## Run a public-facing node

`berzcoind` does not implement `-daemon`; run it under **systemd**, **tmux**, or `nohup`, and use a proper config.

**Do not** copy `configs/mainnet_seeds.toml` directly to `berzcoin.conf`—that file is **TOML** for documentation. The node reads **INI** (`ConfigParser`). Start from **`configs/secure_mainnet.toml`** and set **`dnsseed`** / **`dnsseeds`** to your production seed hostnames (comma-separated).
Note: DNS seeding is disabled by default in shipped nodes to avoid a centralized bootstrap trust. Enable `dnsseed = true` and populate `dnsseeds` or provide a `bootstrap_nodes.json` in your datadir when you run a public node.

```bash
# 1. Example: base secure profile (edit paths, rpcbind, dnsseeds, wallet section as needed)
cp configs/secure_mainnet.toml ~/.berzcoin/berzcoin.conf
# Merge seed names from configs/mainnet_seeds.toml into dnsseeds = host1,host2,...

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

You can also override the passphrase from the shell (avoid on shared machines — it appears in `ps`):

```bash
berzcoind --regtest --walletpassphrase "your-password" -datadir ~/.berzcoin
```

---

## Start node correctly

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
# equivalent network flag if not in file:
berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf
```

`berzcoin-cli` does **not** load RPC settings from `-conf`; pass **`-datadir`** (for the cookie) and **`-rpcport`** to match the node.

---

## Common issues

### “Wallet passphrase required” / failed wallet create

Add **`walletpassphrase = ...`** under **`[main]`** in `berzcoin.conf`, or use **`--walletpassphrase`** on **berzcoind**, or set **`disablewallet = true`**.

### SQLite “database is locked”

Stop **all** processes using that datadir. With **WAL** mode, SQLite also uses sidecar files:

- `regtest.db-wal`
- `regtest.db-shm`

(For **mainnet** the base file is `mainnet.db`.) After a clean shutdown you should not delete these manually; if the node crashed and the DB will not open, **back up** the datadir, then remove the `-wal`/`-shm` files **only** with no process holding the DB.

### Regtest mining / balance

Use **`berzcoin-cli -datadir ~/.berzcoin -rpcport 18443`** (adjust port to your config). Examples:

```bash
berzcoin-cli -datadir ~/.berzcoin -rpcport 18443 unlockwallet "your-passphrase" --timeout 86400
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
