# BerzCoin v2 Defaults Checklist

Freeze date: **2026-04-05 (UTC)**  
Source of truth: `node/app/config.py` (`Config.DEFAULT_CONFIG`) and `node/p2p/dns_seeds.py`.

Use this checklist to confirm release defaults for **mempool**, **network**, and **wallet** are frozen for v2.

## 1. Mempool Defaults (Frozen)

| Key | Default |
|---|---|
| `maxmempool` | `300` (legacy compatibility knob) |
| `mempoolminfee` | `1000` (legacy compatibility knob) |
| `mempool_min_relay_fee` | `1` |
| `mempool_rolling_floor_halflife_secs` | `600` |
| `mempool_max_size_bytes` | `300000000` |
| `mempool_max_weight` | `1500000000` |
| `mempool_max_transactions` | `50000` |
| `mempool_max_ancestors` | `25` |
| `mempool_max_descendants` | `25` |
| `mempool_max_ancestor_size_vbytes` | `101000` |
| `mempool_max_descendant_size_vbytes` | `101000` |
| `mempool_max_package_count` | `25` |
| `mempool_max_package_weight` | `404000` |
| `persistmempool` | `true` |

Checklist:
- [x] `get_mempool_info` returns expected defaults for fresh node.
- [x] `get_mempool_diagnostics` shows policy thresholds and eviction snapshot.
- [x] Legacy knobs (`maxmempool`, `mempoolminfee`) remain backward-compatible.

## 2. Network Defaults (Frozen)

| Key | Default |
|---|---|
| `network` | `mainnet` |
| `bind` | `0.0.0.0` |
| `port` | `8333` |
| `rpcbind` | `127.0.0.1` |
| `rpcport` | `8332` |
| `rpcallowip` | `["127.0.0.1"]` |
| `rpc_require_auth` | `true` |
| `maxconnections` | `125` |
| `maxoutbound` | `8` |
| `dnsseed` | `true` |
| `dnsseeds` | `[]` (no profile fallback; operator must set real seeds) |
| `addnode` | `[]` |
| `connect` | `[]` |
| `bootstrap_enabled` | `true` |
| `bootstrap_file` | `bootstrap_nodes.json` |
| `allow_missing_bootstrap` | `false` |
| `blocksonly` | `false` |
| `lightwallet` | `false` |
| `disable_ip_discovery` | `false` |
| `network_hardening` | `false` |
| `checkpoints` | `true` |
| `node_consensus_version` | `2` |
| `enforce_hardfork_guardrails` | `true` |
| `custom_activation_heights` | `{}` |

DNS seed profile defaults (`node/p2p/dns_seeds.py`):
- `mainnet`: none
- `testnet`: none
- `regtest`: no DNS defaults

Checklist:
- [x] Discovery priority validated: `connect > addnode > bootstrap_file > dnsseed`.
- [x] Non-regtest startup fails fast without viable discovery source.
- [x] RPC bind/auth/IP-filter defaults verified in deployment template.

## 3. Wallet Defaults (Frozen)

| Key | Default |
|---|---|
| `wallet` | `default` |
| `disablewallet` | `false` |
| `wallet_private_key` | `""` |
| `wallet_debug_secrets` | `false` |
| `wallet_encryption_passphrase` | `""` |
| `wallet_default_unlock_timeout` | `300` |
| `mining_require_wallet_match` | `true` |

Checklist:
- [x] `get_wallet_info` does not expose private key/seed in normal mode.
- [x] Wallet-at-rest encryption path is active when passphrase is configured.
- [x] `walletpassphrase` / `walletlock` flow and timeout autolock are verified.

Validation evidence (2026-04-06 UTC):
- `pytest tests/unit/rpc/test_mempool_observability.py -vv`
- `pytest tests/unit/app/test_mempool_config_wiring.py -vv`
- `pytest tests/unit/app/test_bootstrap_defaults.py -vv`
- `pytest tests/unit/rpc/test_wallet_private_key_activation.py -vv`
- `pytest tests/unit/rpc/test_activatewallet_rpc.py -vv`
- `pytest tests/unit/wallet/test_wallet_backup.py -vv`

Additional readiness evidence (2026-04-08 UTC):
- `pytest -q tests/unit -q`
- `pytest -q tests/integration -q`
- `pytest -q tests/e2e -q`
- `pytest -q tests/unit/node/test_indexer_reorg_rollback.py tests/unit/node/test_indexer_wiring.py tests/unit/node/test_reorg_manager.py tests/integration/test_reorg_activation_boundary.py -q`
- `BERZ_SOAK=1 BERZ_SOAK_ITERS=200 pytest -q tests/integration/test_fault_injection_soak.py -q`
- `BERZ_CHAOS_LONG=1 BERZ_CHAOS_LONG_STEPS=1200 pytest -q tests/chaos/test_network_chaos_suite.py::TestNetworkChaosSuite::test_chaos_long_run -q`
- See `docs/MAINNET_READINESS_REPORT_2026-04-08.md` for gate-by-gate results.

## 4. v2 Freeze Sign-off

- [x] RC→release default review completed (deltas documented in `CHANGELOG.md` for `2.0.0`).
- [x] Any default delta is documented in release notes and runbooks.
- [x] `docs/RUNBOOK_DEPLOY.md`, `docs/OPERATIONS_PLAYBOOK.md`, and `docs/rpc-api.md` align with this checklist.
- [x] Release owner sign-off: berzat-babur
- [x] Date (UTC): 2026-04-06
