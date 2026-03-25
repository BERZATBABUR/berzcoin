# BerzCoin

BerzCoin full node, wallet CLI, and shared libraries (Python).

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

# Stratum workers (requires Stratum server on the node)
berzcoin-cli getminingworkers

# Stratum share difficulty
berzcoin-cli setminingdifficulty 0.5
```

### Wallet control

```bash
# Create wallet (optional name; default name is default)
berzcoin-cli createwallet mywallet
berzcoin-cli createwallet --password "secret"

# Load wallet file from datadir/wallets/
berzcoin-cli loadwallet mywallet.dat --password "secret"

# List loaded wallet name(s)
berzcoin-cli listwallets

# Backup (optional directory for backup files)
berzcoin-cli backupwallet --destination /backup/

# List backups
berzcoin-cli listbackups

# Restore from backup name (see listbackups)
berzcoin-cli restorewallet wallet_20240101

# Summary
berzcoin-cli getwalletsummary

# Addresses (--no-include-used to hide used)
berzcoin-cli getwalletaddresses

# UTXOs (--address / --minconf filters)
berzcoin-cli getwalletutxos

# Transactions
berzcoin-cli getwallettransactions --count 50

# Label
berzcoin-cli setwalletlabel "bcrt1q..." "Savings"

# Account
berzcoin-cli createaccount "Trading"

berzcoin-cli lockwallet
berzcoin-cli unlockwallet "password" --timeout 3600

berzcoin-cli getwalletaccounts
```

### Web dashboard (optional)

Enable in config: `webdashboard=true`, `webhost=127.0.0.1`, `webport=8080`, then open `http://127.0.0.1:8080/`.

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

# Wallet (required for dashboard wallet / send unless you use disablewallet = true)
wallet = default
walletpassphrase = your_secure_passphrase_here

# Regtest CPU mining — set miningaddress to a real regtest address before enabling autostart
mining = true
miningaddress = bcrt1qyour_mining_address_here
autominer = true
```

Notes:

- The correct key is **`walletpassphrase`**, not `walletpassword`.
- **`miningaddress`** must be a valid regtest address. If the wallet is new, start once with `mining = false` and `autominer = false`, then run `berzcoin-cli unlockwallet ...`, `berzcoin-cli getnewaddress`, `berzcoin-cli setminingaddress "<address>"`, set `miningaddress` in the conf (or rely on RPC), and set `mining` / `autominer` as needed; restart if you only changed the file.
- Dashboard **Start/Stop mining** buttons are **regtest-only**; use `network = regtest` or start with **`berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf`**.
- Mining autostart needs an unlocked wallet when the address is tied to wallet policy; unlock via **`berzcoin-cli unlockwallet`** if the miner refuses to start.

Start the node:

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
# or explicitly:
berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf
```

Then open **http://127.0.0.1:8080/**.

More detail: [docs/QUICK_START.md](docs/QUICK_START.md).

## License

See LICENSE.
