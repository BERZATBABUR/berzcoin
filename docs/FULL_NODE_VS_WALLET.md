# Full node: wallet disabled vs wallet enabled

This document describes **BerzCoin** behavior (not Bitcoin Core). Flags and config keys differ.

## Full node with wallet disabled (`--disablewallet`)

With **`berzcoind --regtest --disablewallet`** (and matching **`datadir`**), you run the chain stack **without** loading the embedded wallet:

| Area | Role |
|------|------|
| Chain DB | `regtest.db` (or `<network>.db`), migrations, headers / chainwork |
| UTXO set | Global chain UTXO (not wallet tracking) |
| Mempool | Accept/validate txs, relay when P2P is up |
| P2P | Peers, sync loop, DNS seeds (if `dnsseed=true`) |
| JSON-RPC | Cookie auth; many chain/mempool methods |
| Block storage | Block store under datadir |

You **do not** get: `getbalance`, `getnewaddress`, `sendtoaddress`, `listunspent`, etc., because there is **no loaded wallet**.

## Full node with wallet enabled

Remove **`--disablewallet`**, set **`disablewallet = false`** in config, then activate with **`activatewallet <private_key_hex>`**.

That adds: keystore, local UTXO tracking for the wallet, tx build/sign, and wallet RPC handlers.

## CLI and “regtest”

**`berzcoin-cli` does not implement `--regtest`.** The RPC server does not care about the name “regtest”; it talks to **whatever node is listening** on **`-rpcconnect` / `-rpcport`**.

Use the same **datadir** as the node (for **`.cookie`**) and the **same RPC port** as in the node’s config:

```bash
berzcoin-cli -datadir ~/.berzcoin -rpcport 8332 getblockchaininfo
```

If the node uses **`rpcport = 18443`** in `berzcoin.conf`, use **`-rpcport 18443`**.

### Examples that match this repo’s CLI

Subcommands exist for things like **`getblockchaininfo`**, **`getblockcount`**, **`getblock`**, **`generate`** (regtest), **`setgenerate`**, etc. There is **no** `berzcoin-cli getmempoolinfo` subcommand in the stock CLI; mempool calls are exposed on JSON-RPC as **`get_mempool_info`**, **`get_raw_mempool`**, **`send_raw_transaction`** — call them via **`curl`** / a small script, or add CLI parsers if you want.

```bash
# Blockchain (CLI wrapper exists)
berzcoin-cli -datadir ~/.berzcoin -rpcport 8332 getblockchaininfo
berzcoin-cli -datadir ~/.berzcoin -rpcport 8332 getblockcount
```

Raw JSON-RPC example (adjust port and cookie):

```bash
curl -s --user berzcoin:$(cut -d: -f2 ~/.berzcoin/.cookie) \
  -d '{"jsonrpc":"2.0","method":"get_mempool_info","params":[],"id":1}' \
  -H 'content-type: application/json' \
  http://127.0.0.1:8332/
```

## Config file format

Node config is **INI** (`[main]` or any section name; keys are merged), **not** TOML arrays.

DNS seeds are a **comma-separated** list:

```ini
[main]
network = mainnet
dnsseed = true
dnsseeds = seed1.example.org,seed2.example.org
```

There is **no** `bootstrap_nodes = [...]` key in the default `Config` today; bootstrap peer lists are documented separately (e.g. `configs/bootstrap_nodes.json` / tooling).

## Starting the node (no `-daemon`)

**`berzcoind` does not support `-daemon` or `--daemon` in this codebase.** Run in **tmux**, **systemd**, or **`nohup`**, or foreground.

There is **no** `--mainnet` flag; **mainnet** is the default **`network`** unless you set **`network = testnet`** / **`regtest`** in config or use **`--testnet` / `--regtest`**.

Example:

```bash
berzcoind -conf ~/.berzcoin/berzcoin.conf
# or
berzcoind --regtest -conf ~/.berzcoin/berzcoin.conf
```

## Regtest mining

- **`generate`** and **`setgenerate`** RPC are **regtest-only** in this implementation.
- You need a **miner** (regtest always initializes one) and, for **`setgenerate`**, an active private-key wallet identity plus a valid **`miningaddress`** where applicable.

## Mainnet “production” checklist (high level)

1. **Real peers**: DNS names in **`dnsseeds`** must resolve to reachable **P2P** listeners; placeholder hostnames will fail DNS.
2. **Network params**: `shared/consensus/params.py` / genesis artifacts as used by your deployment.
3. **RPC security**: **`rpcbind`**, **`rpcallowip`**, cookie file permissions; avoid exposing private keys in shell history.
4. **Process manager**: systemd unit, user account, `Restart=`, logging rotation — operational, not built into `berzcoind`.

## Optional ops (not in core `berzcoind`)

- **Prometheus / health**: add externally or extend the node; not required for a full node.
- **Dedicated DNS seed software**: separate from the P2P node; `scripts/update_dns_seeds.py` only refreshes **`configs/bootstrap_nodes.json`** from hostnames.

For a guided regtest profile (wallet file + config), see **`scripts/setup_regtest.sh`** and **`docs/QUICK_START.md`**.

---

## Analysis: what is still missing for a *resilient* public full node

Your process already runs the **full-node component set** (DB, chainstate, mempool, P2P, RPC).  
For v0.1 scope, **lightwallet and stratum are removed**; the remaining gaps are about **joining a real network** and **day‑2 ops**.

### 1. Peer bootstrap beyond DNS

- **Implemented:** **`addnode`**, **`connect`**, **`bootstrap_file`**, **`bootstrap_enabled`** in **`Config`**; **`ConnectionManager`** loads JSON **`bootstrap_nodes`** into **`AddrMan`** on startup and honors **`connect`** (connect‑only: no DNS outbound discovery, no **`getaddr`** fan‑out).
- **`scripts/update_dns_seeds.py`** still writes **`configs/bootstrap_nodes.json`**; copy or symlink that file into **`datadir`** (or set **`bootstrap_file`** to an absolute path) so the node can load it.

### 2. Structured bootstrap vs. today’s sync loop

- **`Bootstrap.run()`** is now invoked from **`BerzCoinNode.start()`** when **`chainstate.get_best_height() == -1`** (before the background sync task), with a **300s** timeout; the periodic **`_run_sync_loop`** still runs afterward.
- Empty chain (**`height == -1`**) is a valid initial state; **`BlockSync._build_locator`** only includes hashes for **`height >= 0`**, so **the first headers sync depends on peers being well‑behaved** when local height is still invalid/empty.

**What to add:** Optionally run **`Bootstrap.run()`** once after P2P starts, or guarantee genesis/header entry conditions in your deployment docs.

### 3. Genesis / block 0 on a fresh datadir

- **`scripts/test_send_receive.py`** documents that you need **a chain tip** (e.g. genesis in the index) before some flows work.
- There is **no automatic “insert consensus genesis block at height 0”** step obvious in **`ChainState.initialize()`**—fresh nodes stay at **`height=-1`** until something (mining, import, or peer) supplies the first blocks.

**What to add:** A one‑time **genesis import** or documented **bootstrap from first peer**; align **`shared/consensus/params.py`** genesis fields with whatever you store in **`genesis/*.json`**.

### 4. CLI parity for operators

- **Added:** **`getmempoolinfo`**, **`getrawmempool`**, **`sendrawtransaction`**, **`testmempoolaccept`**, **`getmempoolentry`** in **`berzcoin-cli`** (see **`cli/commands/mempool.py`**).

### 5. Wallet vs. chain‑only (recap)

- **Chain‑only:** `disablewallet` / **`--disablewallet`** — no balance/send RPC from the embedded wallet.
- **Full node + wallet:** passphrase + wallet file as documented elsewhere.

### Summary table

| Area | Status today | Typical “add” for production |
|------|----------------|-------------------------------|
| Full validation + storage | Present | Keep validating full-node profile |
| P2P | Present | **Real DNS + `addnode` / `bootstrap_nodes.json` / `connect`** |
| Mempool / relay | Present | Ensure peers (same as P2P) |
| JSON‑RPC | Present | Lock down bind/allowlist |
| Bootstrap file | Loaded from **datadir** if enabled | Maintain JSON + DNS |
| `Bootstrap` orchestration | Run once when height **-1** (timeout 300s) | Tune timeout if needed |
| Genesis / height 0 | Still manual / peer‑dependent | Consensus‑accurate import not auto‑done |
| Operator CLI | Mempool subcommands added | Extend as needed |

None of this replaces **`network`**, **`datadir`**, or **`dnsseed`/`dnsseeds`**—those remain the first‑class config knobs for *where* you run.
